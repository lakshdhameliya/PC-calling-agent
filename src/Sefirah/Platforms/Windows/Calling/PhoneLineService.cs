using System.Collections.Concurrent;
using CommunityToolkit.WinUI;
using Sefirah.Data.AppDatabase.Repository;
using Sefirah.Data.Models;
using Sefirah.Platforms.Windows.Bluetooth;
using Sefirah.Platforms.Windows.Utilities;
using Windows.ApplicationModel.Calls;
using Windows.Devices.Enumeration;
using Windows.Devices.Radios;

namespace Sefirah.Platforms.Windows.Calling;

public sealed class PhoneLineService(
    ILogger logger,
    BluetoothRadioManager bluetoothRadioManager,
    IDeviceManager deviceManager,
    DeviceRepository deviceRepository) : IPhoneLineService
{
    private const string ContainerIdKey = "System.Devices.ContainerId";
    private const string BluetoothDeviceAddressKey = "System.DeviceInterface.Bluetooth.DeviceAddress";

    private readonly ConcurrentDictionary<string, DeviceInformation> pltDeviceById = new();
    private readonly ConcurrentDictionary<Guid, PhoneLine> phoneLinesByLineId = new();

    private DeviceWatcher? pltDeviceWatcher;
    private PhoneLineWatcher? phoneLineWatcher;
    private CancellationTokenSource? refreshCts;

    private PairedDevice? ActiveDevice => deviceManager.ActiveDevice;
    private string? ActiveTransportId => ActiveDevice?.CallsTransportDeviceId;
    private readonly bool supportsCallControl = CallingFeatureUtils.SupportsCallControlApis();

    public CallingLineStatus LineStatus { get; private set; } = CallingLineStatus.NotLinked;

    public event EventHandler<CallingLineStatus>? LineStatusChanged;
    public event EventHandler<IPhoneCall>? CallStateChanged;

    public async Task InitializeAsync()
    {
        var contractOk = CallingFeatureUtils.IsBluetoothCallingSupportedByPlatform();
        if (!contractOk)
            logger.Warn("CallsPhoneContract v5 not present on this OS.");

        var transportApisOk = CallingFeatureUtils.TryUnlockPhoneLineTransportDeviceAPIs(logger);
        if (!transportApisOk)
            logger.Warn("Phone line transport LAF not unlocked.");

        var callingSupported = contractOk && transportApisOk;
        if (!callingSupported) 
        {
            SetLineStatus(CallingLineStatus.NotSupported);
            return;
        }

        if (!supportsCallControl)
            logger.Info("CallsPhoneContract v6 not present");

        // Continue starting the call watchers even if the radio API returns
        // an empty snapshot during startup; RefreshStateAsync retries discovery.
        await bluetoothRadioManager.RefreshAsync();

        deviceManager.ActiveDeviceChanged += OnActiveDeviceChanged;
        bluetoothRadioManager.RadioStateChanged += OnBluetoothRadioStateChanged;

        try
        {
            pltDeviceWatcher = DeviceInformation.CreateWatcher(PhoneLineTransportDevice.GetDeviceSelector(), [BluetoothDeviceAddressKey]);
            pltDeviceWatcher.Added += OnPltDeviceAdded;
            pltDeviceWatcher.Removed += OnPltDeviceRemoved;
            pltDeviceWatcher.Updated += OnPltDeviceUpdated;
            pltDeviceWatcher.Start();
            logger.Debug("Phone line transport watcher started");
        }
        catch (Exception ex)
        {
            logger.Error("Error creating phone line transport watcher", ex);
        }

        await StartPhoneLineWatcherAsync();

        if (supportsCallControl)
            PhoneCallManager.CallStateChanged += OnCallStateChanged;

        _ = RefreshStateAsync();
    }

    private async Task StartPhoneLineWatcherAsync()
    {
        try
        {
            var store = await PhoneCallManager.RequestStoreAsync();
            var defaultLineId = await store.GetDefaultLineAsync();
            if (defaultLineId != Guid.Empty)
            {
                var defaultLine = await PhoneLine.FromIdAsync(defaultLineId);
                if (defaultLine is not null)
                {
                    phoneLinesByLineId[defaultLineId] = defaultLine;
                }
            }

            phoneLineWatcher = store.RequestLineWatcher();
            phoneLineWatcher.LineAdded += OnPhoneLineAdded;
            phoneLineWatcher.LineRemoved += OnPhoneLineRemoved;
            phoneLineWatcher.Stopped += OnPhoneLineWatcherStopped;
            phoneLineWatcher.LineUpdated += OnPhoneLineUpdated;
            phoneLineWatcher.Start();
        }
        catch (Exception ex)
        {
            logger.Error("Failed to start PhoneLine watcher", ex);
            phoneLineWatcher = null;
        }
    }

    private async void OnPhoneLineUpdated(PhoneLineWatcher sender, PhoneLineWatcherEventArgs args)
    {
        var line = await PhoneLine.FromIdAsync(args.LineId);
        if (line is null) return;

        logger.Debug($"PhoneLine updated {args.LineId} (DisplayName={line.DisplayName})");
        phoneLinesByLineId[args.LineId] = line;
        await RefreshStateAsync();
        if (supportsCallControl)
            CheckActiveCalls(line);
    }

    private void OnPhoneLineWatcherStopped(PhoneLineWatcher sender, object args) => logger.Warn("PhoneLine watcher stopped");

    private async void OnPhoneLineAdded(PhoneLineWatcher sender, PhoneLineWatcherEventArgs args)
    {
        var line = await PhoneLine.FromIdAsync(args.LineId);
        if (line is null) return; 

        logger.Debug($"PhoneLine added/updated {args.LineId} (DisplayName={line.DisplayName})");
        phoneLinesByLineId[args.LineId] = line;
        await RefreshStateAsync();
        if (supportsCallControl)
            CheckActiveCalls(line);
    }

    private async void OnPhoneLineRemoved(PhoneLineWatcher sender, PhoneLineWatcherEventArgs args)
    {
        logger.Debug($"PhoneLine removed {args.LineId}");
        phoneLinesByLineId.TryRemove(args.LineId, out _);
        await RefreshStateAsync();
    }

    private void OnCallStateChanged(object? sender, object e) => CheckActiveCalls();

    private async void CheckActiveCalls(PhoneLine line)
    {
        try
        {
            var result = await line.GetAllActivePhoneCallsAsync();
            foreach (var activeCall in result.AllActivePhoneCalls)
            {
                if (string.IsNullOrEmpty(line.TransportDeviceId))
                    continue;

                if (activeCall.Status is global::Windows.ApplicationModel.Calls.PhoneCallStatus.Ended or global::Windows.ApplicationModel.Calls.PhoneCallStatus.Lost)
                    continue;

                CallStateChanged?.Invoke(this, new PhoneCall(activeCall, line.TransportDeviceId));
            }
        }
        catch (Exception ex)
        {
            logger.Error($"Failed to read active calls for line {line.Id}", ex);
        }
    }

    private async void CheckActiveCalls()
    {
        var seenCallIds = new HashSet<string>(StringComparer.Ordinal);

        foreach (var (lineId, line) in phoneLinesByLineId)
        {
            try
            {
                var result = await line.GetAllActivePhoneCallsAsync();
                foreach (var activeCall in result.AllActivePhoneCalls)
                {
                    if (activeCall.Status is global::Windows.ApplicationModel.Calls.PhoneCallStatus.Ended or global::Windows.ApplicationModel.Calls.PhoneCallStatus.Lost)
                        continue;

                    if (seenCallIds.Add(activeCall.CallId))
                    {
                        if (string.IsNullOrEmpty(line.TransportDeviceId))
                            continue;

                        CallStateChanged?.Invoke(this, new PhoneCall(activeCall, line.TransportDeviceId));
                    }
                }
            }
            catch (Exception ex)
            {
                logger.Error($"Failed to read active calls for line {lineId}", ex);
            }
        }
    }

    public async Task DialAsync(string phoneNumber, CancellationToken cancellationToken = default)
    {
        if (ActiveDevice is null || string.IsNullOrWhiteSpace(phoneNumber))
            return;

        // Only attempt native Windows PhoneLine dial if the transport is actually linked and ready
        if (LineStatus is CallingLineStatus.Ready && !string.IsNullOrEmpty(ActiveTransportId))
        {
            var line = FindLineByTransport(ActiveTransportId);
            if (line is not null)
            {
                try
                {
                    if (!supportsCallControl)
                    {
                        // contract v5
                        line.Dial(phoneNumber, phoneNumber);
                        return;
                    }

                    var result = await line.DialWithResultAsync(phoneNumber, phoneNumber);
                    var phoneCall = result.DialedCall;

                    if (phoneCall is not null)
                    {
                        var audioStatus = await phoneCall.ChangeAudioDeviceAsync(PhoneCallAudioDevice.LocalDevice);
                        logger.Info($"Dialed {phoneNumber}; call={phoneCall.CallId}, audio-route={audioStatus}.");
                        if (phoneCall.CallId != "0" && audioStatus.ToString() != "InvalidCallState")
                        {
                            CallStateChanged?.Invoke(this, new PhoneCall(phoneCall, ActiveTransportId!));
                            return;
                        }
                        logger.Warn($"Native call returned invalid audio state ({audioStatus}) or call id ({phoneCall.CallId}); falling back to companion app.");
                    }
                    else
                    {
                        logger.Warn($"DialWithResultAsync returned no call object for {phoneNumber}; status={result.DialCallStatus}. Falling back to companion app.");
                    }
                }
                catch (Exception ex)
                {
                    logger.Warn("Native DialAsync failed; routing through companion app instead", ex);
                }
            }
        }

        logger.Info($"Sending remote dial request for {phoneNumber} to {ActiveDevice.Name} (LineStatus={LineStatus}).");
        ActiveDevice.SendMessage(new CallControlRequest { Action = "dial", PhoneNumber = phoneNumber });
    }

    public async Task<IPhoneCall?> GetActiveCallAsync(string? phoneNumber = null)
    {
        var normalized = string.IsNullOrWhiteSpace(phoneNumber)
            ? string.Empty
            : new string(phoneNumber.Where(char.IsDigit).ToArray());

        foreach (var (_, line) in phoneLinesByLineId)
        {
            try
            {
                var result = await line.GetAllActivePhoneCallsAsync();
                foreach (var activeCall in result.AllActivePhoneCalls)
                {
                    if (string.IsNullOrEmpty(line.TransportDeviceId))
                        continue;

                    if (activeCall.Status is global::Windows.ApplicationModel.Calls.PhoneCallStatus.Ended or global::Windows.ApplicationModel.Calls.PhoneCallStatus.Lost)
                        continue;

                    if (string.IsNullOrEmpty(normalized))
                    {
                        return new PhoneCall(activeCall, line.TransportDeviceId);
                    }

                    try
                    {
                        var info = await activeCall.GetPhoneCallInfoAsync();
                        var callNorm = string.IsNullOrWhiteSpace(info?.PhoneNumber)
                            ? string.Empty
                            : new string(info.PhoneNumber.Where(char.IsDigit).ToArray());

                        if (string.IsNullOrEmpty(callNorm) ||
                            callNorm.EndsWith(normalized, StringComparison.Ordinal) ||
                            normalized.EndsWith(callNorm, StringComparison.Ordinal))
                        {
                            return new PhoneCall(activeCall, line.TransportDeviceId);
                        }
                    }
                    catch
                    {
                        return new PhoneCall(activeCall, line.TransportDeviceId);
                    }
                }
            }
            catch (Exception ex)
            {
                logger.Error("Failed to query active phone calls", ex);
            }
        }

        return null;
    }

    private async Task RefreshActiveCallsAfterDialAsync(PhoneLine line)
    {
        await Task.Delay(700);
        CheckActiveCalls(line);
    }

    private async void OnActiveDeviceChanged(object? sender, PairedDevice? device)
    {
        refreshCts?.Cancel();
        refreshCts?.Dispose();
        refreshCts = new CancellationTokenSource();
        var ct = refreshCts.Token;

        try
        {
            await RefreshStateAsync(ct);
        }
        catch (OperationCanceledException) { }
    }

    public async Task RefreshStateAsync(CancellationToken cancellationToken = default)
    { 
        if (!await bluetoothRadioManager.RefreshAsync())
        {
            SetLineStatus(CallingLineStatus.BluetoothAdapterNotFound);
            return;
        }

        if (bluetoothRadioManager.RadioState is not RadioState.On) 
        {
            SetLineStatus(CallingLineStatus.BluetoothAdapterOff);
            return;
        }

        var device = ActiveDevice;
        if (device is null)
        {
            return;
        }

        var transportId = device.CallsTransportDeviceId;

        if (string.IsNullOrWhiteSpace(transportId))
        {
            transportId = await FindTransportAsync(device.BluetoothAddress, device.Name);
            if (string.IsNullOrWhiteSpace(transportId))
            {
                SetLineStatus(CallingLineStatus.DeviceNotPaired);
                return;
            }
            await SaveTransportIdAsync(device, transportId);
        }

        var status = await ConnectTransportAsync(transportId, cancellationToken);
        if (status is CallingLineStatus.TransportMissing or CallingLineStatus.NotLinked)
        {
            var fallbackTransportId = await FindTransportAsync(device.BluetoothAddress, device.Name);
            if (!string.IsNullOrWhiteSpace(fallbackTransportId) &&
                !string.Equals(fallbackTransportId, transportId, StringComparison.OrdinalIgnoreCase))
            {
                logger.Info($"Updating stale transport ID from {transportId} to {fallbackTransportId}");
                transportId = fallbackTransportId;
                await SaveTransportIdAsync(device, transportId);
                status = await ConnectTransportAsync(transportId, cancellationToken);
            }
        }

        SetLineStatus(status);
    }

    private async Task<CallingLineStatus> ConnectTransportAsync(string transportDeviceId, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(transportDeviceId)) return CallingLineStatus.NotLinked;

        try
        {
            var pltDevice = PhoneLineTransportDevice.FromId(transportDeviceId);
            if (pltDevice is null)
            {
                logger.Debug($"phoneline was null for {transportDeviceId}");
                return CallingLineStatus.TransportMissing;
            }

            var access = await pltDevice.RequestAccessAsync();
            cancellationToken.ThrowIfCancellationRequested();
            if (access is not DeviceAccessStatus.Allowed)
            {
                logger.Debug($"Access not allowed for {transportDeviceId}: {access}");
                return CallingLineStatus.RegistrationAccessDenied;
            }

            if (!pltDevice.IsRegistered())
                pltDevice.RegisterApp();

            if (!pltDevice.IsRegistered())
            {
                logger.Warn($"Registration failed for {transportDeviceId} — another app may own this line.");
                return CallingLineStatus.RegistrationAccessDenied;
            }

            // Check if a dialable PhoneLine is already available or registered by Windows
            var line = await EnsureLineForTransportAsync(transportDeviceId);
            if (line is not null)
            {
                logger.Info($"PhoneLine is ready for transport {transportDeviceId} (DisplayName={line.DisplayName})");
                return CallingLineStatus.Ready;
            }

            var connected = await pltDevice.ConnectAsync();
            line = await EnsureLineForTransportAsync(transportDeviceId);
            if (line is not null)
            {
                logger.Info($"PhoneLine ready after ConnectAsync for {transportDeviceId} (DisplayName={line.DisplayName})");
                return CallingLineStatus.Ready;
            }

            if (!connected)
            {
                logger.Warn($"ConnectAsync returned false and no dialable line for {transportDeviceId}");
                return CallingLineStatus.NotLinked;
            }

            return CallingLineStatus.Ready;
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception ex)
        {
            logger.Warn($"ConnectTransportAsync failed for {transportDeviceId}", ex);
            return CallingLineStatus.NotLinked;
        }
    }

    private void SetLineStatus(CallingLineStatus status)
    {
        LineStatus = status;
        LineStatusChanged?.Invoke(this, status);
    }

    private async void OnBluetoothRadioStateChanged(RadioState radioState)
    {   
        await RefreshStateAsync();
    }

    private async void OnPltDeviceAdded(DeviceWatcher sender, DeviceInformation args)
    {
        logger.Debug($"PLT watcher added transport {args.Id} (Name={args.Name})");
        pltDeviceById[args.Id] = args;
        await RefreshStateAsync();
    }

    private async void OnPltDeviceUpdated(DeviceWatcher sender, DeviceInformationUpdate args)
    {
        if (pltDeviceById.TryGetValue(args.Id, out var deviceInfo))
        {
            deviceInfo.Update(args);
            logger.Debug($"PLT watcher updated transport {args.Id}");
            await RefreshStateAsync();
        }
    }

    private async void OnPltDeviceRemoved(DeviceWatcher sender, DeviceInformationUpdate args)
    {
        logger.Debug($"PLT watcher removed transport {args.Id}");
        pltDeviceById.TryRemove(args.Id, out _);

        await RefreshStateAsync();
    }

    private async Task SaveTransportIdAsync(PairedDevice device, string? transportId)
    {
        // DeviceWatcher callbacks run on a worker thread. PairedDevice is an
        // ObservableObject bound to WinUI, so raising PropertyChanged here
        // causes RPC_E_WRONG_THREAD (0x8001010E) and terminates the app when a
        // second Bluetooth/phone-line device is added.
        await App.MainWindow.DispatcherQueue.EnqueueAsync(() =>
            device.CallsTransportDeviceId = transportId);

        var entity = await deviceRepository.GetPairedDevice(device.Id).ConfigureAwait(false);
        if (entity is null) return;
        entity.CallsTransportDeviceId = transportId;
        if (!string.IsNullOrEmpty(transportId) && pltDeviceById.TryGetValue(transportId, out var dInfo))
        {
            if (dInfo.Properties.TryGetValue(BluetoothDeviceAddressKey, out var addrObj) && addrObj is string addr && !string.IsNullOrWhiteSpace(addr))
            {
                entity.BluetoothAddress = addr;
                await App.MainWindow.DispatcherQueue.EnqueueAsync(() =>
                    device.BluetoothAddress = addr);
            }
        }
        deviceRepository.AddOrUpdateRemoteDevice(entity);
    }

    private async Task<string?> FindTransportAsync(string? btAddress, string deviceName)
    {
        if (TryFindTransport(btAddress, deviceName, out var transportId))
        {
            return transportId;
        }

        try
        {
            var selector = PhoneLineTransportDevice.GetDeviceSelector();
            var devices = await DeviceInformation.FindAllAsync(selector, [BluetoothDeviceAddressKey]);
            foreach (var d in devices)
            {
                pltDeviceById[d.Id] = d;
            }

            if (TryFindTransport(btAddress, deviceName, out transportId))
            {
                return transportId;
            }

            if (devices.Count == 1)
            {
                return devices[0].Id;
            }
        }
        catch (Exception ex)
        {
            logger.Warn("Failed to enumerate phone line transport devices", ex);
        }

        return null;
    }

    private PhoneLine? FindLineByTransport(string transportDeviceId) =>
        phoneLinesByLineId.Values.Where(p => p.CanDial).FirstOrDefault(pl => string.Equals(pl.TransportDeviceId, transportDeviceId, StringComparison.OrdinalIgnoreCase));

    private async Task<PhoneLine?> EnsureLineForTransportAsync(string transportDeviceId)
    {
        var line = FindLineByTransport(transportDeviceId);
        if (line is not null) return line;

        try
        {
            var store = await PhoneCallManager.RequestStoreAsync();
            var defaultLineId = await store.GetDefaultLineAsync();
            if (defaultLineId != Guid.Empty)
            {
                var defaultLine = await PhoneLine.FromIdAsync(defaultLineId);
                if (defaultLine is not null)
                {
                    phoneLinesByLineId[defaultLineId] = defaultLine;
                    if (string.Equals(defaultLine.TransportDeviceId, transportDeviceId, StringComparison.OrdinalIgnoreCase))
                    {
                        return defaultLine;
                    }
                }
            }
        }
        catch (Exception ex)
        {
            logger.Debug("EnsureLineForTransportAsync failed to query default line", ex);
        }

        return FindLineByTransport(transportDeviceId);
    }

    private bool TryFindTransport(string? btAddress, string deviceName, out string transportId)
    {
        if (TryFindTransportByBluetoothAddress(btAddress, out transportId))
        {
            return true;
        }

        if (TryFindTransportByName(deviceName, out transportId))
        {
            return true;
        }

        return false;
    }

    private bool TryFindTransportByBluetoothAddress(string? bluetoothAddress, out string transportId)
    {
        transportId = string.Empty;
        if (string.IsNullOrWhiteSpace(bluetoothAddress))
            return false;

        var matches = pltDeviceById.Values
            .Where(d => DoBluetoothCallingAddressesMatch(d, bluetoothAddress))
            .Select(d => d.Id)
            .Where(id => !string.IsNullOrWhiteSpace(id))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
        if (matches.Length == 1)
        {
            transportId = matches[0];
            return true;
        }

        return false;
    }

    private bool TryFindTransportByName(string deviceName, out string transportId)
    {
        transportId = string.Empty;

        var byWatcher = pltDeviceById.Values
            .Where(d => IsNameMatch(deviceName, d.Name))
            .Select(d => d.Id)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
        if (byWatcher.Length == 1)
        {
            transportId = byWatcher[0];
            return true;
        }

        var byPhoneLine = phoneLinesByLineId.Values
            .Where(line => IsNameMatch(deviceName, line.DisplayName))
            .Select(line => line.TransportDeviceId)
            .Where(id => !string.IsNullOrWhiteSpace(id))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
        if (byPhoneLine.Length == 1)
        {
            transportId = byPhoneLine[0];
            return true;
        }

        return false;
    }

    // Phone-line transport watcher entries expose the Bluetooth MAC on the interface key, while AEP
    // address can be null in this watcher context. Use the interface address for transport matching.
    private static bool DoBluetoothCallingAddressesMatch(DeviceInformation incomingDeviceInformation, string bluetoothAddress)
    {
        var normalizedStoredAddress = bluetoothAddress.Replace(":", string.Empty);
        if (incomingDeviceInformation.Properties.TryGetValue(BluetoothDeviceAddressKey, out var value) && value is string incomingBluetoothAddress)
        {
            return string.Equals(normalizedStoredAddress, incomingBluetoothAddress, StringComparison.OrdinalIgnoreCase);
        }

        return false;
    }


    private static bool IsNameMatch(string target, string? candidate) =>
        !string.IsNullOrEmpty(candidate) &&
        string.Equals(target.Trim(), candidate.Trim(), StringComparison.OrdinalIgnoreCase);
}
