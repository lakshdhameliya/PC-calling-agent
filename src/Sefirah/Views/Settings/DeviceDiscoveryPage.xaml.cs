using Sefirah.Data.Contracts;
using Sefirah.Data.Items;
using Sefirah.Data.Models;
using Sefirah.Helpers;
using Sefirah.Utils.Serialization;

namespace Sefirah.Views.Settings;

public sealed partial class DeviceDiscoveryPage : Page
{
    private readonly ISessionManager SessionManager = Ioc.Default.GetRequiredService<ISessionManager>();
    private readonly IDiscoveryService DiscoveryService = Ioc.Default.GetRequiredService<IDiscoveryService>();
    private CancellationTokenSource? _adbScanCts;
    private string _currentPairingPin = "";

    public DeviceDiscoveryPage()
    {
        InitializeComponent();
        SetupBreadcrumb();
    }

    private async void Page_Loaded(object sender, RoutedEventArgs e)
    {
        // Try auto-connecting to any advertised Wireless Debugging endpoint
        try
        {
            var autoEndpoint = await WirelessAdbCallController.ScanMdnsConnectAsync();
            if (!string.IsNullOrWhiteSpace(autoEndpoint))
            {
                await WirelessAdbCallController.ConnectAsync(autoEndpoint);
            }
        }
        catch { }

        await UpdateAdbStatusHeaderAsync();
    }

    private async Task UpdateAdbStatusHeaderAsync()
    {
        try
        {
            var connected = await WirelessAdbCallController.GetConnectedDeviceAsync();
            if (!string.IsNullOrWhiteSpace(connected))
            {
                AdbButtonLabel.Text = "ADB Connected";
                AdbButtonIcon.Glyph = "\uE73E"; // Checkmark
            }
            else
            {
                AdbButtonLabel.Text = "Pair Wireless ADB";
                AdbButtonIcon.Glyph = "\uE8EA"; // Phone
            }
        }
        catch { }
    }

    private void SetupBreadcrumb()
    {
        BreadcrumbBar.ItemsSource = new ObservableCollection<BreadcrumbBarItemModel>
        {
            new("Devices.Title".GetLocalizedResource(), typeof(DevicesPage)),
            new("AvailableDevices/Title".GetLocalizedResource(), typeof(DeviceDiscoveryPage))
        };
        BreadcrumbBar.ItemClicked += BreadcrumbBar_ItemClicked;
    }

    private void BreadcrumbBar_ItemClicked(BreadcrumbBar sender, BreadcrumbBarItemClickedEventArgs args)
    {
        var items = BreadcrumbBar.ItemsSource as ObservableCollection<BreadcrumbBarItemModel>;
        var clickedItem = items?[args.Index];

        if (clickedItem?.PageType is not null && clickedItem.PageType != typeof(DeviceDiscoveryPage))
        {
            if (Frame.CanGoBack)
            {
                Frame.GoBack();
            }
        }
    }

    private void ConnectButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button button && button.Tag is DiscoveredDevice device)
        {
            SessionManager.Pair(device);
        }
    }

    private async void QrCodeButton_Click(object sender, RoutedEventArgs e)
    {
        QrCodeButton.IsEnabled = false;
        QrCodeImage.Source = null;
        QrCodeStatusText.Text = "Preparing QR code…";
        try
        {
            var bitmapImage = await DiscoveryService.GenerateQrCodeAsync();
            if (bitmapImage is null)
            {
                QrCodeStatusText.Text = "QR code is not ready. Check your network and try again.";
                return;
            }

            QrCodeImage.Source = bitmapImage;
            QrCodeStatusText.Text = "Scan this QR code to connect";
        }
        finally
        {
            QrCodeButton.IsEnabled = true;
        }
    }

    private void AdbPairButton_Click(object sender, RoutedEventArgs e)
    {
        // Handled by AdbPairFlyout_Opened
    }

    private async void AdbPairFlyout_Opened(object sender, object e)
    {
        _adbScanCts?.Cancel();
        _adbScanCts = new CancellationTokenSource();
        var ct = _adbScanCts.Token;

        AdbProgressRing.Visibility = Visibility.Visible;
        AdbProgressRing.IsActive = true;
        AdbStatusIcon.Visibility = Visibility.Collapsed;
        AdbStatusText.Text = "Checking ADB connection...";

        // 1. First try auto-connecting to any already-advertised mDNS connect endpoint
        var autoEndpoint = await WirelessAdbCallController.ScanMdnsConnectAsync();
        if (!string.IsNullOrWhiteSpace(autoEndpoint))
        {
            await WirelessAdbCallController.ConnectAsync(autoEndpoint);
        }

        // 2. Check if device is now online
        var existing = await WirelessAdbCallController.GetConnectedDeviceAsync();
        if (!string.IsNullOrWhiteSpace(existing))
        {
            AdbProgressRing.Visibility = Visibility.Collapsed;
            AdbProgressRing.IsActive = false;
            AdbStatusIcon.Visibility = Visibility.Visible;
            AdbStatusText.Text = $"Connected to {existing} (ADB Online & Ready)";
            AdbButtonLabel.Text = "ADB Connected";
            AdbButtonIcon.Glyph = "\uE73E";
            return;
        }

        // 3. Pre-fill IP address if available
        try
        {
            var deviceManager = Ioc.Default.GetService<IDeviceManager>();
            var paired = deviceManager?.PairedDevices.FirstOrDefault();
            if (paired != null && !string.IsNullOrWhiteSpace(paired.Address))
            {
                AdbEndpointBox.Text = $"{paired.Address}:";
            }
        }
        catch { }

        // 4. Generate QR code payload
        _currentPairingPin = Random.Shared.Next(100000, 999999).ToString();
        var serviceName = $"studio-sefirah-{Random.Shared.Next(1000, 9999)}";
        var payload = $"WIFI:T:ADB;S:{serviceName};P:{_currentPairingPin};;";

        var qrBytes = ImageHelper.GenerateQrCode(payload);
        if (qrBytes != null)
        {
            AdbQrImage.Source = await qrBytes.ToBitmapAsync(180);
        }

        AdbProgressRing.Visibility = Visibility.Collapsed;
        AdbProgressRing.IsActive = false;
        AdbStatusText.Text = "Click '1-Click Auto Connect' or enter pairing code below.";
        AdbQrHint.Text = $"PIN: {_currentPairingPin}";

        // 5. Background auto-connect / auto-pairing loop
        _ = Task.Run(async () =>
        {
            try
            {
                while (!ct.IsCancellationRequested)
                {
                    // Check if phone is already broadcasting connect service
                    var connEp = await WirelessAdbCallController.ScanMdnsConnectAsync();
                    if (!string.IsNullOrWhiteSpace(connEp))
                    {
                        await WirelessAdbCallController.ConnectAsync(connEp);
                    }

                    // Check if an online device appeared
                    var dev = await WirelessAdbCallController.GetConnectedDeviceAsync();
                    if (!string.IsNullOrWhiteSpace(dev))
                    {
                        DispatcherQueue.TryEnqueue(() =>
                        {
                            AdbProgressRing.Visibility = Visibility.Collapsed;
                            AdbProgressRing.IsActive = false;
                            AdbStatusIcon.Visibility = Visibility.Visible;
                            AdbStatusText.Text = $"Connected to {dev} (ADB Online & Ready)";
                            AdbButtonLabel.Text = "ADB Connected";
                            AdbButtonIcon.Glyph = "\uE73E";
                        });
                        break;
                    }

                    // Also check for pairing announcement if user used pairing
                    var pairEndpoint = await WirelessAdbCallController.ScanMdnsPairingAsync();
                    if (!string.IsNullOrWhiteSpace(pairEndpoint))
                    {
                        DispatcherQueue.TryEnqueue(() =>
                        {
                            AdbProgressRing.Visibility = Visibility.Visible;
                            AdbProgressRing.IsActive = true;
                            AdbStatusText.Text = $"Pairing with {pairEndpoint}...";
                        });

                        var pairRes = await WirelessAdbCallController.PairAsync(pairEndpoint, _currentPairingPin);
                        if (pairRes.ExitCode == 0 || pairRes.Output.Contains("success", StringComparison.OrdinalIgnoreCase))
                        {
                            await Task.Delay(1000, ct);
                            var nextConn = await WirelessAdbCallController.ScanMdnsConnectAsync();
                            if (!string.IsNullOrWhiteSpace(nextConn))
                            {
                                await WirelessAdbCallController.ConnectAsync(nextConn);
                            }
                            else
                            {
                                var host = pairEndpoint.Split(':')[0];
                                await WirelessAdbCallController.ConnectAsync($"{host}:5555");
                            }

                            var connected = await WirelessAdbCallController.GetConnectedDeviceAsync();
                            DispatcherQueue.TryEnqueue(() =>
                            {
                                AdbProgressRing.Visibility = Visibility.Collapsed;
                                AdbProgressRing.IsActive = false;
                                AdbStatusIcon.Visibility = Visibility.Visible;
                                AdbStatusText.Text = $"Successfully connected to {connected ?? pairEndpoint}!";
                                AdbButtonLabel.Text = "ADB Connected";
                                AdbButtonIcon.Glyph = "\uE73E";
                            });
                            break;
                        }
                    }

                    await Task.Delay(1500, ct);
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception ex)
            {
                DispatcherQueue.TryEnqueue(() =>
                {
                    AdbStatusText.Text = $"Note: {ex.Message}";
                });
            }
        }, ct);
    }

    private void AdbPairFlyout_Closed(object sender, object e)
    {
        _adbScanCts?.Cancel();
    }

    private async void AdbPairSubmitButton_Click(object sender, RoutedEventArgs e)
    {
        var endpoint = AdbEndpointBox.Text?.Trim() ?? "";
        var code = AdbCodeBox.Text?.Trim() ?? "";

        if (string.IsNullOrWhiteSpace(endpoint) || string.IsNullOrWhiteSpace(code))
        {
            AdbStatusText.Text = "કૃપા કરીને IP Address:Port અને Pairing Code દાખલ કરો.";
            return;
        }

        AdbProgressRing.Visibility = Visibility.Visible;
        AdbProgressRing.IsActive = true;
        AdbStatusIcon.Visibility = Visibility.Collapsed;
        AdbStatusText.Text = $"Pairing with {endpoint}...";
        AdbPairSubmitButton.IsEnabled = false;

        try
        {
            var res = await WirelessAdbCallController.PairAsync(endpoint, code);
            if (res.ExitCode == 0 || res.Output.Contains("success", StringComparison.OrdinalIgnoreCase))
            {
                AdbStatusText.Text = "Paired! Connecting to device...";
                await Task.Delay(1000);

                var connEndpoint = await WirelessAdbCallController.ScanMdnsConnectAsync();
                if (!string.IsNullOrWhiteSpace(connEndpoint))
                {
                    await WirelessAdbCallController.ConnectAsync(connEndpoint);
                }
                else
                {
                    var host = endpoint.Split(':')[0];
                    await WirelessAdbCallController.ConnectAsync($"{host}:5555");
                }

                var connected = await WirelessAdbCallController.GetConnectedDeviceAsync();
                AdbProgressRing.Visibility = Visibility.Collapsed;
                AdbProgressRing.IsActive = false;
                AdbStatusIcon.Visibility = Visibility.Visible;
                AdbStatusText.Text = $"Connected to {connected ?? endpoint}! (ADB Ready)";
                AdbButtonLabel.Text = "ADB Connected";
                AdbButtonIcon.Glyph = "\uE73E";
            }
            else
            {
                AdbProgressRing.Visibility = Visibility.Collapsed;
                AdbProgressRing.IsActive = false;
                AdbStatusText.Text = $"Pairing failed: {res.Output.Trim()}";
            }
        }
        catch (Exception ex)
        {
            AdbProgressRing.Visibility = Visibility.Collapsed;
            AdbProgressRing.IsActive = false;
            AdbStatusText.Text = $"Error: {ex.Message}";
        }
        finally
        {
            AdbPairSubmitButton.IsEnabled = true;
        }
    }

    private async void AdbRefreshButton_Click(object sender, RoutedEventArgs e)
    {
        AdbProgressRing.Visibility = Visibility.Visible;
        AdbProgressRing.IsActive = true;
        AdbStatusIcon.Visibility = Visibility.Collapsed;
        AdbStatusText.Text = "Scanning and connecting to phone...";

        // 1. Try mDNS connect endpoint
        var autoEndpoint = await WirelessAdbCallController.ScanMdnsConnectAsync();
        if (!string.IsNullOrWhiteSpace(autoEndpoint))
        {
            await WirelessAdbCallController.ConnectAsync(autoEndpoint);
        }

        var connected = await WirelessAdbCallController.GetConnectedDeviceAsync();
        AdbProgressRing.Visibility = Visibility.Collapsed;
        AdbProgressRing.IsActive = false;

        if (!string.IsNullOrWhiteSpace(connected))
        {
            AdbStatusIcon.Visibility = Visibility.Visible;
            AdbStatusText.Text = $"Connected to {connected} (ADB Online & Ready)";
            AdbButtonLabel.Text = "ADB Connected";
            AdbButtonIcon.Glyph = "\uE73E";
        }
        else
        {
            AdbStatusText.Text = "No ADB device connected. Turn on Wireless Debugging on phone.";
            AdbButtonLabel.Text = "Pair Wireless ADB";
            AdbButtonIcon.Glyph = "\uE8EA";
        }
    }
}
