using CommunityToolkit.WinUI;
using System.Collections.Concurrent;
using System.Text.Json;
using Sefirah.Data.AppDatabase.Repository;
using Sefirah.Data.Models;
using Sefirah.ViewModels;
using Sefirah.Views.WindowViews;

namespace Sefirah.Features;

public class CallFeature(
    IPlatformNotificationHandler platformNotificationHandler,
    IPhoneLineService phoneLineService,
    CallLogRepository callLogRepository,
    ContactRepository contactRepository,
    IDeviceManager deviceManager,
    ILogger logger) : ICallFeature
{
    private CallWindow? ActiveCallWindow { get; set; }
    private readonly ConcurrentDictionary<string, DateTimeOffset> recentBluetoothIncomingCalls = new(StringComparer.Ordinal);
    private CancellationTokenSource? autoAnswerCts;
    private string? autoAnswerPhoneNumber;
    private static readonly object CallStateFileLock = new();
    public CallSessionViewModel? PrimaryCall { get; private set; }
    public CallSessionViewModel? SecondaryCall { get; private set; }

    public event EventHandler? ActiveCallChanged;

    private static void NotifyCallState(string state, string phoneNumber)
    {
        try
        {
            var dir = Path.Combine(AppContext.BaseDirectory, "VoiceAgent", "AIPhoneVoiceBot_NoADB_POC");
            if (!Directory.Exists(dir))
                dir = @"C:\Frontbit Link\VoiceAgent\AIPhoneVoiceBot_NoADB_POC";
            if (Directory.Exists(dir))
            {
                var path = Path.Combine(dir, "call_state.json");
                lock (CallStateFileLock)
                {
                    var tempPath = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
                    var json = JsonSerializer.Serialize(new
                    {
                        state,
                        phone_number = phoneNumber,
                        timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                    });

                    // Replace in one operation so the Python bot never reads a half-written JSON file.
                    File.WriteAllText(tempPath, json);
                    File.Move(tempPath, path, true);
                }
            }
        }
        catch { }
    }

    public Task InitializeAsync()
    {
        phoneLineService.CallStateChanged += OnPhoneLineCallStateChanged;
        _ = WarmUpWirelessAdbAsync();
        return Task.CompletedTask;
    }

    private async Task WarmUpWirelessAdbAsync()
    {
        // Keep trying in the background so a phone that is paired/scanned after
        // the desktop app starts reconnects without a manual adb command.
        for (var attempt = 0; attempt < 60; attempt++)
        {
            try
            {
                var device = deviceManager.ActiveDevice
                    ?? deviceManager.PairedDevices.FirstOrDefault(d => d.IsConnected);
                if (device is not null && await WirelessAdbCallController.EnsureConnectedAsync(device))
                {
                    logger.Info("Wireless ADB call-control device is connected and ready.");
                    return;
                }
            }
            catch (Exception ex)
            {
                logger.Debug("Wireless ADB startup connection attempt failed.", ex);
            }

            await Task.Delay(TimeSpan.FromSeconds(1));
        }
    }

    public async Task HandleCallInfoAsync(PairedDevice device, CallInfo callInfo)
    {
        logger.Info($"Received Android call-state event ({callInfo.CallState}) from {device.Name}; HFP status={phoneLineService.LineStatus}.");

        var normalizedNumber = NormalizePhoneNumber(callInfo.PhoneNumber);
        if (callInfo.CallState is CallState.Ringing
            && normalizedNumber.Length > 0
            && recentBluetoothIncomingCalls.TryGetValue(normalizedNumber, out var bluetoothTime)
            && DateTimeOffset.UtcNow - bluetoothTime < TimeSpan.FromSeconds(15))
        {
            logger.Debug("Suppressed duplicate Android ringing event because the Bluetooth call notification is already active.");
            return;
        }

        if (callInfo.CallState is CallState.Ringing)
        {
            // Start pickup before notifications/contact lookup/UI work.
            ScheduleAutoAnswer(device, callInfo.PhoneNumber);
            NotifyCallState("ringing", callInfo.PhoneNumber);
            var callTag = BuildCallTag(device.Id, callInfo.PhoneNumber);
            await platformNotificationHandler.ShowRemoteCallNotification(
                device.Id,
                callTag,
                callInfo.PhoneNumber,
                callInfo.ContactInfo?.DisplayName);

            var nativeCall = await phoneLineService.GetActiveCallAsync(callInfo.PhoneNumber);

            await App.MainWindow.DispatcherQueue.EnqueueAsync(() =>
            {
                if (PrimaryCall is null)
                {
                    var contact = contactRepository.GetContact(
                        device.Id,
                        callInfo.PhoneNumber,
                        callInfo.ContactInfo?.DisplayName);

                    var remoteCall = new RemotePhoneCall(device, callInfo.PhoneNumber, contact.DisplayName, CallingPhoneCallStatus.Incoming)
                    {
                        NativeCall = nativeCall
                    };
                    var newSession = new CallSessionViewModel(remoteCall, contact);
                    PrimaryCall = newSession;
                    newSession.SessionEnded += OnSessionEnded;
                    ActiveCallChanged?.Invoke(this, EventArgs.Empty);
                    ActiveCallWindow ??= new CallWindow();
                    UpdateActiveCallWindowSize();
                    ActiveCallWindow.Activate();
                    logger.Info($"Created incoming call session (with nativeCall={nativeCall != null}) and displayed CallWindow for {callInfo.PhoneNumber}");
                }
                else if (PrimaryCall.PhoneCall is RemotePhoneCall remote && remote.NativeCall is null && nativeCall is not null)
                {
                    remote.NativeCall = nativeCall;
                }
            });
            return;
        }

        if (callInfo.CallState is CallState.InProgress)
        {
            CancelAutoAnswer();
            NotifyCallState("talking", callInfo.PhoneNumber);
            var callTag = BuildCallTag(device.Id, callInfo.PhoneNumber);
            await platformNotificationHandler.RemoveNotificationByTag(callTag);

            var nativeCall = await phoneLineService.GetActiveCallAsync(callInfo.PhoneNumber);

            await App.MainWindow.DispatcherQueue.EnqueueAsync(async () =>
            {
                if (PrimaryCall is null)
                {
                    var contact = contactRepository.GetContact(
                        device.Id,
                        callInfo.PhoneNumber,
                        callInfo.ContactInfo?.DisplayName);

                    var remoteCall = new RemotePhoneCall(device, callInfo.PhoneNumber, contact.DisplayName, CallingPhoneCallStatus.Talking)
                    {
                        NativeCall = nativeCall
                    };
                    var newSession = new CallSessionViewModel(remoteCall, contact);
                    PrimaryCall = newSession;
                    newSession.SessionEnded += OnSessionEnded;
                    ActiveCallChanged?.Invoke(this, EventArgs.Empty);
                    ActiveCallWindow ??= new CallWindow();
                    UpdateActiveCallWindowSize();
                    ActiveCallWindow.Activate();
                    logger.Info($"Created remote call session and displayed CallWindow for {callInfo.PhoneNumber}");
                }
                else if (PrimaryCall.PhoneCall is RemotePhoneCall remote)
                {
                    remote.NativeCall ??= nativeCall;
                    remote.Status = CallingPhoneCallStatus.Talking;
                    logger.Info($"Updated existing call session to Talking for {callInfo.PhoneNumber}");
                }

                if (nativeCall is not null)
                {
                    try
                    {
                        await nativeCall.ChangeAudioDeviceAsync(CallingPhoneCallAudioDevice.LocalDevice);
                        logger.Info($"Routed Bluetooth audio to LocalDevice for {callInfo.PhoneNumber}");
                    }
                    catch (Exception ex)
                    {
                        logger.Warn("Failed to route Bluetooth audio to LocalDevice", ex);
                    }
                }
            });
            return;
        }

        if (callInfo.CallState is CallState.Ended or CallState.MissedCall)
        {
            CancelAutoAnswer();
            NotifyCallState("idle", callInfo.PhoneNumber);
            var callTag = BuildCallTag(device.Id, callInfo.PhoneNumber);
            await platformNotificationHandler.RemoveNotificationByTag(callTag);
            await App.MainWindow.DispatcherQueue.EnqueueAsync(() =>
            {
                EndAllCalls();
            });
            return;
        }

        try
        {
            var callTag = BuildCallTag(device.Id, callInfo.PhoneNumber);

            if (callInfo.ContactInfo is not null)
                await contactRepository.SaveContactAsync(device.Id, callInfo.ContactInfo);

            var contact = contactRepository.GetContact(
                device.Id,
                callInfo.PhoneNumber,
                callInfo.ContactInfo?.DisplayName);

            var icon = contact.HasAvatar ? await contact.GetToastAvatarUriAsync() : null;
            var title = callInfo.CallState is CallState.Ringing ? "CallIncoming".GetLocalizedResource() : "CallMissed".GetLocalizedResource();
            if (callInfo.CallState is CallState.Ringing)
            {
                await platformNotificationHandler.ShowRemoteCallNotification(title, contact.DisplayName, callTag, device.Id, icon);
            }
            else
            {
                await platformNotificationHandler.ShowCallNotification(title, contact.DisplayName, callTag, callInfo.CallState, icon);
            }
        }
        catch (Exception ex)
        {
            logger.Error($"Error handling call info from device {device.Id}", ex);
        }
    }

    private static string BuildCallTag(string deviceId, string phoneNumber) => $"call_{deviceId}_{phoneNumber}";

    private static string NormalizePhoneNumber(string? phoneNumber) =>
        string.IsNullOrWhiteSpace(phoneNumber)
            ? string.Empty
            : new string(phoneNumber.Where(char.IsDigit).ToArray());

    private void ScheduleAutoAnswer(PairedDevice device, string phoneNumber)
    {
        var normalized = NormalizePhoneNumber(phoneNumber);
        if (string.IsNullOrEmpty(normalized))
            return;

        CancelAutoAnswer();
        autoAnswerPhoneNumber = normalized;
        var cts = new CancellationTokenSource();
        autoAnswerCts = cts;
        _ = AutoAnswerWithRetryAsync(device, phoneNumber, cts.Token);
    }

    private void CancelAutoAnswer()
    {
        autoAnswerCts?.Cancel();
        autoAnswerCts?.Dispose();
        autoAnswerCts = null;
        autoAnswerPhoneNumber = null;
    }

    private async Task AutoAnswerWithRetryAsync(PairedDevice device, string phoneNumber, CancellationToken cancellationToken)
    {
        // The Android socket event can arrive just before the UI session is created.
        // Retry briefly instead of relying on a single fragile timer callback.
        try
        {
            if (await WirelessAdbCallController.AcceptIncomingCallAsync(device))
            {
                logger.Info($"Wireless ADB accepted incoming call for {phoneNumber}.");
                return;
            }

            for (var attempt = 1; attempt <= 6; attempt++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (attempt > 1)
                    await Task.Delay(TimeSpan.FromMilliseconds(100), cancellationToken);
                if (await WirelessAdbCallController.AcceptIncomingCallAsync(device))
                    return;
                await App.MainWindow.DispatcherQueue.EnqueueAsync(async () =>
                {
                    var sessionNumber = NormalizePhoneNumber(PrimaryCall?.CallerNumber);
                    if (PrimaryCall is not null
                        && PrimaryCall.PhoneCall.Status == CallingPhoneCallStatus.Incoming
                        && (sessionNumber == autoAnswerPhoneNumber
                            || sessionNumber.EndsWith(autoAnswerPhoneNumber ?? string.Empty, StringComparison.Ordinal)
                            || (autoAnswerPhoneNumber ?? string.Empty).EndsWith(sessionNumber, StringComparison.Ordinal)))
                    {
                        logger.Info($"Fallback auto-answer attempt {attempt}/6 for {phoneNumber}.");
                        await PrimaryCall.AcceptCallCommand.ExecuteAsync(null);
                    }
                });

                if (PrimaryCall?.PhoneCall.Status is CallingPhoneCallStatus.Talking)
                    return;
            }

            logger.Warn($"Auto-answer did not reach Talking state after retries for {phoneNumber}.");
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            logger.Warn($"Auto-answer worker failed for {phoneNumber}.", ex);
        }
    }

    private void ScheduleNativeAdbAnswer(IPhoneCall call, string phoneNumber)
    {
        CancelAutoAnswer();
        var cts = new CancellationTokenSource();
        autoAnswerCts = cts;
        autoAnswerPhoneNumber = NormalizePhoneNumber(phoneNumber);
        _ = NativeAdbAnswerWithRetryAsync(call, phoneNumber, cts.Token);
    }

    private async Task NativeAdbAnswerWithRetryAsync(IPhoneCall call, string phoneNumber, CancellationToken cancellationToken)
    {
        try
        {
            var device = deviceManager.FindDeviceByTransportId(call.TransportDeviceId)
                ?? deviceManager.ActiveDevice;
            if (device is null)
            {
                logger.Warn("Cannot auto-answer native incoming call because no paired phone device is available.");
                return;
            }

            for (var attempt = 1; attempt <= 10; attempt++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (call.Status is not CallingPhoneCallStatus.Incoming)
                    return;

                logger.Info($"Wireless ADB native auto-answer attempt {attempt}/10 for {phoneNumber}.");
                if (await WirelessAdbCallController.AcceptIncomingCallAsync(device))
                {
                    logger.Info($"Wireless ADB accepted native incoming call on attempt {attempt}.");
                    return;
                }

                await Task.Delay(TimeSpan.FromMilliseconds(400), cancellationToken);
            }

            logger.Warn("Wireless ADB could not accept the native incoming call after retries.");
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            logger.Warn("Native Wireless ADB auto-answer worker failed.", ex);
        }
    }

    private async void OnPhoneLineCallStateChanged(object? sender, IPhoneCall call)
    {
        var callId = call.CallId;
        try
        {
            switch (call.Status)
            {
                case CallingPhoneCallStatus.Ended:
                case CallingPhoneCallStatus.Lost:
                    CancelAutoAnswer();
                    NotifyCallState("idle", "");
                    await platformNotificationHandler.RemoveNotificationsByTagAndGroup(callId, Constants.Notification.IncomingPhoneCallGroup);
                    await App.MainWindow.DispatcherQueue.EnqueueAsync(() =>
                    {
                        if (PrimaryCall?.CallId == callId ||
                            (PrimaryCall?.PhoneCall as RemotePhoneCall)?.NativeCall?.CallId == callId)
                        {
                            OnSessionEnded(PrimaryCall, EventArgs.Empty);
                        }
                        else if (SecondaryCall?.CallId == callId ||
                            (SecondaryCall?.PhoneCall as RemotePhoneCall)?.NativeCall?.CallId == callId)
                        {
                            OnSessionEnded(SecondaryCall, EventArgs.Empty);
                        }
                    });
                    call.Dispose();
                    break;

                case CallingPhoneCallStatus.Incoming:
                {
                    var title = "CallIncoming".GetLocalizedResource();
                    var (displayName, phoneNumber, avatarUri) = await GetContactInfoAsync(call);
                    var transportDeviceId = call.TransportDeviceId;
                    var normalizedNumber = NormalizePhoneNumber(phoneNumber);
                    if (normalizedNumber.Length > 0)
                    {
                        recentBluetoothIncomingCalls[normalizedNumber] = DateTimeOffset.UtcNow;
                        await platformNotificationHandler.RemoveNotificationByTag(
                            BuildCallTag(ResolveDeviceId(call), phoneNumber));
                    }

                    logger.Info($"Received Bluetooth incoming call state for {callId}.");

                    await App.MainWindow.DispatcherQueue.EnqueueAsync(() =>
                    {
                        if (PrimaryCall?.PhoneCall is RemotePhoneCall remote)
                        {
                            var primaryNorm = NormalizePhoneNumber(PrimaryCall.CallerNumber);
                            if (string.IsNullOrEmpty(primaryNorm) || primaryNorm == normalizedNumber ||
                                primaryNorm.EndsWith(normalizedNumber, StringComparison.Ordinal) ||
                                normalizedNumber.EndsWith(primaryNorm, StringComparison.Ordinal))
                            {
                                remote.NativeCall = call;
                                logger.Info($"Attached native call {callId} to existing RemotePhoneCall session.");
                                return;
                            }
                        }

                        if (PrimaryCall is null)
                        {
                            var callerContact = contactRepository.GetContact(
                                ResolveDeviceId(call),
                                phoneNumber,
                                displayName);
                            var newSession = new CallSessionViewModel(call, callerContact);
                            PrimaryCall = newSession;
                            newSession.SessionEnded += OnSessionEnded;
                            ActiveCallChanged?.Invoke(this, EventArgs.Empty);
                            ActiveCallWindow ??= new CallWindow();
                            UpdateActiveCallWindowSize();
                            ActiveCallWindow.Activate();
                            logger.Info($"Created native Bluetooth incoming call session and displayed CallWindow for {phoneNumber}");
                        }
                    });

                    // The visible incoming window can be produced by the native
                    // Windows call watcher even when no remote CallInfo event is
                    // delivered. In that case the old command path never runs.
                    // Use the same Wi-Fi ADB controller directly for pickup.
                    NotifyCallState("ringing", phoneNumber);
                    ScheduleNativeAdbAnswer(call, phoneNumber);

                    try
                    {
                        await platformNotificationHandler.ShowCallNotification(callId, transportDeviceId, title, displayName, avatarUri);
                    }
                    catch (Exception ex)
                    {
                        logger.Debug($"Failed to show incoming call notification for {callId}.", ex);
                    }
                    break;
                }

                default:
                    if (call.Status is CallingPhoneCallStatus.Talking)
                    {
                        CancelAutoAnswer();
                        NotifyCallState("talking", string.Empty);
                    }
                    await App.MainWindow.DispatcherQueue.EnqueueAsync(async () =>
                    {
                        try
                        {
                            if (PrimaryCall?.PhoneCall is RemotePhoneCall remote)
                            {
                                var primaryNorm = NormalizePhoneNumber(PrimaryCall.CallerNumber);
                                var (_, pNumber, _) = await GetContactInfoAsync(call);
                                var callNorm = NormalizePhoneNumber(pNumber);
                                if (string.IsNullOrEmpty(primaryNorm) || primaryNorm == callNorm ||
                                    primaryNorm.EndsWith(callNorm, StringComparison.Ordinal) ||
                                    callNorm.EndsWith(primaryNorm, StringComparison.Ordinal))
                                {
                                    remote.NativeCall ??= call;
                                    remote.Status = CallingPhoneCallStatus.Talking;
                                    logger.Info($"Linked native call {callId} to primary RemotePhoneCall in Talking status.");

                                    try
                                    {
                                        await call.ChangeAudioDeviceAsync(CallingPhoneCallAudioDevice.LocalDevice);
                                        logger.Info($"Routed Bluetooth audio to LocalDevice for native call {callId}");
                                    }
                                    catch (Exception ex)
                                    {
                                        logger.Warn("Failed to route Bluetooth audio to LocalDevice in default handler", ex);
                                    }
                                    return;
                                }
                            }

                            if (PrimaryCall?.CallId == callId || SecondaryCall?.CallId == callId)
                            {
                                call.Dispose();
                                return;
                            }

                            CallSessionViewModel? newSession = null;

                            if (PrimaryCall is null)
                            {
                                var callerContact = await GetContactAsync(call);
                                newSession = new CallSessionViewModel(call, callerContact);
                                PrimaryCall = newSession;
                            }
                            else if (SecondaryCall is null)
                            {
                                var callerContact = await GetContactAsync(call);
                                newSession = new CallSessionViewModel(call, callerContact);
                                SecondaryCall = newSession;
                            }
                            else
                            {
                                // more than 2 calls isn't supported
                                call.Dispose();
                                return;
                            }

                            newSession.SessionEnded += OnSessionEnded;

                            // Route audio to LocalDevice (PC Bluetooth SCO) when call connects
                            try
                            {
                                await call.ChangeAudioDeviceAsync(CallingPhoneCallAudioDevice.LocalDevice);
                                logger.Info($"Routed active call {callId} to LocalDevice");
                            }
                            catch (Exception ex)
                            {
                                logger.Warn("Failed to route active call to LocalDevice", ex);
                            }

                            // Keep the currently active (non-held) call in the primary slot.
                            // When a second call arrives and becomes active, promote it to primary.
                            if (PrimaryCall is not null && SecondaryCall is not null
                                && PrimaryCall.PhoneCall.Status is CallingPhoneCallStatus.Held
                                && SecondaryCall.PhoneCall.Status is not CallingPhoneCallStatus.Held)
                            {
                                (PrimaryCall, SecondaryCall) = (SecondaryCall, PrimaryCall);
                            }

                            await platformNotificationHandler.RemoveNotificationsByTagAndGroup(callId, Constants.Notification.IncomingPhoneCallGroup);

                            ActiveCallChanged?.Invoke(this, EventArgs.Empty);
                            ActiveCallWindow ??= new CallWindow();
                            UpdateActiveCallWindowSize();
                        }
                        catch (Exception ex)
                        {
                            logger.Error($"Failed to create call session for {callId}.", ex);
                            call.Dispose();
                        }
                    });
                    break;
            }
        }
        catch (Exception ex)
        {
            logger.Warn($"Failed to handle call state change for {callId}.", ex);
            call.Dispose();
        }
    }

    private void OnSessionEnded(object? sender, EventArgs e)
    {
        var endedCallId = (sender as CallSessionViewModel)?.CallId;
        if (string.IsNullOrWhiteSpace(endedCallId))
        {
            return;
        }

        var sessionsChanged = false;

        if (PrimaryCall?.CallId == endedCallId)
        {
            var disposedPrimary = PrimaryCall;
            disposedPrimary.SessionEnded -= OnSessionEnded;
            disposedPrimary.Dispose();
            PrimaryCall = SecondaryCall;
            SecondaryCall = null;
            sessionsChanged = true;
        }
        else if (SecondaryCall?.CallId == endedCallId)
        {
            var disposedSecondary = SecondaryCall;
            disposedSecondary.SessionEnded -= OnSessionEnded;
            disposedSecondary.Dispose();
            SecondaryCall = null;
            sessionsChanged = true;
        }

        if (!sessionsChanged)
        {
            return;
        }

        CancelAutoAnswer();

        ActiveCallChanged?.Invoke(this, EventArgs.Empty);

        if (PrimaryCall is null)
        {
            NotifyCallState("idle", "");
            App.MainWindow.DispatcherQueue.TryEnqueue(() =>
            {
                try { ActiveCallWindow?.Close(); } catch { }
                ActiveCallWindow = null;
            });
        }
        else
        {
            UpdateActiveCallWindowSize();
        }
    }

    public async Task SwapCallsAsync()
    {
        if (PrimaryCall is null || SecondaryCall is null)
            return;

        if (PrimaryCall.PhoneCall.Status is CallingPhoneCallStatus.Talking)
            await PrimaryCall.PhoneCall.HoldAsync();

        if (SecondaryCall.PhoneCall.Status is CallingPhoneCallStatus.Held)
            await SecondaryCall.PhoneCall.ResumeFromHoldAsync();

        (PrimaryCall, SecondaryCall) = (SecondaryCall, PrimaryCall);
        ActiveCallChanged?.Invoke(this, EventArgs.Empty);
    }

    public void EndCall(string? phoneNumber = null)
    {
        var normalized = NormalizePhoneNumber(phoneNumber);

        if (!string.IsNullOrEmpty(normalized) && SecondaryCall is not null && NormalizePhoneNumber(SecondaryCall.CallerNumber) == normalized)
        {
            var s = SecondaryCall;
            SecondaryCall = null;
            s.SessionEnded -= OnSessionEnded;
            s.Dispose();
            ActiveCallChanged?.Invoke(this, EventArgs.Empty);
            UpdateActiveCallWindowSize();
            return;
        }

        if (string.IsNullOrEmpty(normalized) || PrimaryCall is null || NormalizePhoneNumber(PrimaryCall.CallerNumber) == normalized || SecondaryCall is null)
        {
            if (PrimaryCall is not null)
            {
                var p = PrimaryCall;
                p.SessionEnded -= OnSessionEnded;
                p.Dispose();
                PrimaryCall = SecondaryCall;
                SecondaryCall = null;
                ActiveCallChanged?.Invoke(this, EventArgs.Empty);
            }

            if (PrimaryCall is null)
            {
                ActiveCallWindow?.Close();
                ActiveCallWindow = null;
            }
            else
            {
                UpdateActiveCallWindowSize();
            }
            return;
        }
    }

    public void EndAllCalls()
    {
        NotifyCallState("idle", "");
        App.MainWindow.DispatcherQueue.TryEnqueue(() =>
        {
            if (PrimaryCall is not null)
            {
                var p = PrimaryCall;
                PrimaryCall = null;
                p.SessionEnded -= OnSessionEnded;
                p.Dispose();
            }
            if (SecondaryCall is not null)
            {
                var s = SecondaryCall;
                SecondaryCall = null;
                s.SessionEnded -= OnSessionEnded;
                s.Dispose();
            }
            ActiveCallChanged?.Invoke(this, EventArgs.Empty);
            try { ActiveCallWindow?.Close(); } catch { }
            ActiveCallWindow = null;
        });
    }

    private void UpdateActiveCallWindowSize()
    {
        ActiveCallWindow?.UpdateWindowSize(SecondaryCall is not null);
    }

    private async Task<Contact> GetContactAsync(IPhoneCall call)
    {
        var info = await call.GetPhoneCallInfoAsync();
        return contactRepository.GetContact(ResolveDeviceId(call), info.PhoneNumber, info.DisplayName);
    }

    private async Task<(string displayName, string phoneNumber, Uri? avatarUri)> GetContactInfoAsync(IPhoneCall call)
    {
        var info = await call.GetPhoneCallInfoAsync();
        var contact = contactRepository.GetContact(ResolveDeviceId(call), info.PhoneNumber, info.DisplayName);
        var avatarUri = contact.HasAvatar ? await contact.GetToastAvatarUriAsync() : null;
        return (contact.DisplayName, info.PhoneNumber, avatarUri);
    }

    private string? ResolveDeviceId(IPhoneCall call) => deviceManager.FindDeviceByTransportId(call.TransportDeviceId)?.Id ?? deviceManager.ActiveDevice?.Id;
}
