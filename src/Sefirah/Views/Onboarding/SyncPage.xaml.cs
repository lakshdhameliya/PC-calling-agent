using Microsoft.UI.Xaml.Media.Animation;
using Sefirah.Data.Contracts;
using Sefirah.Data.Models;
using Sefirah.Helpers;
using Sefirah.Utils.Serialization;
using Sefirah.ViewModels.Settings;

namespace Sefirah.Views.Onboarding;

public sealed partial class SyncPage : Page
{
    public DevicesViewModel ViewModel { get; }

    private readonly ISessionManager SessionManager = Ioc.Default.GetRequiredService<ISessionManager>();
    private readonly IDiscoveryService DiscoveryService = Ioc.Default.GetRequiredService<IDiscoveryService>();

    public SyncPage()
    {
        InitializeComponent();
        ViewModel = Ioc.Default.GetRequiredService<DevicesViewModel>();
        Loaded += SyncPage_Loaded;
    }

    private async void SyncPage_Loaded(object sender, RoutedEventArgs e)
    {
        await GenerateQrCodeAsync();
    }

    private void SkipButton_Click(object sender, RoutedEventArgs e)
    {
        // Mark onboarding as completed
        AppLocalSettings.Set("HasCompletedOnboarding", true);
        Frame.Navigate(typeof(MainPage), null, new DrillInNavigationTransitionInfo());
    }

    private async Task GenerateQrCodeAsync()
    {
        QrCodeStatusText.Text = "Preparing QR code…";
        try
        {
            var bitmapImage = await DiscoveryService.GenerateQrCodeAsync();
            if (bitmapImage is not null)
            {
                QrCodeImage.Source = bitmapImage;
                QrCodeImage.Visibility = Visibility.Visible;
                QrCodeStatusText.Text = "Scan this QR code to connect";
            }
            else
            {
                QrCodeImage.Source = null;
                QrCodeImage.Visibility = Visibility.Collapsed;
                QrCodeStatusText.Text = "QR code is not ready. Check your network and try again.";
            }
        }
        catch (Exception ex)
        {
            QrCodeImage.Source = null;
            QrCodeImage.Visibility = Visibility.Collapsed;
            QrCodeStatusText.Text = "Could not create the QR code. Please try again.";
            System.Diagnostics.Debug.WriteLine($"QR code creation failed: {ex}");
        }
    }

    private async void ConnectButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button button && button.Tag is DiscoveredDevice device)
        {
            SessionManager.Pair(device);
        }
    }
}
