using Microsoft.Windows.AppNotifications;
using Microsoft.Windows.AppNotifications.Builder;
using Sefirah.Data.Contracts;
using Windows.Services.Store;
using WinRT.Interop;
using static Sefirah.Constants;

namespace Sefirah.Platforms.Windows.Services;
public partial class UpdateService : ObservableObject, IUpdateService
{
    private const string UpdateNotificationTag = "app-update";
    private const string UpdateNotificationGroup = "update";

    private StoreContext? storeContext;
    private List<StorePackageUpdate>? updatePackages = [];

    private bool isUpdateAvailable;
    public bool IsUpdateAvailable
    {
        get => isUpdateAvailable;
        set => SetProperty(ref isUpdateAvailable, value);
    }

    private bool isUpdating;
    public bool IsUpdating
    {
        get => isUpdating;
        private set => SetProperty(ref isUpdating, value);
    }

    public bool IsMandatory => updatePackages?.Where(e => e.Mandatory).ToList().Count >= 1;

    public async Task CheckForUpdatesAsync()
    {
        // Disabled for this build so update checks do not contact Microsoft Store.
        await Task.CompletedTask;
        updatePackages = [];
        IsUpdateAvailable = false;
    }

    private static void ShowUpdateAvailableNotification()
    {
        try
        {
            var builder = new AppNotificationBuilder()
                .AddText("UpdateNotification.Title".GetLocalizedResource())
                .AddText("UpdateNotification.Subtitle".GetLocalizedResource())
                .SetTag(UpdateNotificationTag)
                .SetGroup(UpdateNotificationGroup)
                .AddButton(new AppNotificationButton("UpdateNotification.Action".GetLocalizedResource())
                    .AddArgument("notificationType", ToastNotificationType.Update)
                    .AddArgument("action", "download"));

            var notification = builder.BuildNotification();
            notification.ExpiresOnReboot = true;
            AppNotificationManager.Default.Show(notification);
        }
        catch
        {
            // Notification may fail if not registered; ignore
        }
    }

    public async Task DownloadUpdatesAsync()
    {
        // Disabled for this build so updates are never installed automatically.
        await Task.CompletedTask;
        IsUpdating = false;
    }

    private async Task GetUpdatePackagesAsync()
    {
        try
        {
            storeContext ??= await Task.Run(StoreContext.GetDefault);

            InitializeWithWindow.Initialize(storeContext, App.WindowHandle);

            var updateList = await storeContext.GetAppAndOptionalStorePackageUpdatesAsync();
            updatePackages = updateList?.ToList();
        }
        catch (Exception)
        {
            // GetAppAndOptionalStorePackageUpdatesAsync throws for unknown reasons.
        }
    }
}
