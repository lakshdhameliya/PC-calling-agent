using Windows.System;
using static Sefirah.Constants;

namespace Sefirah.Views.Onboarding;

public sealed partial class WelcomePage : Page
{
    public WelcomePage()
    {
        InitializeComponent();
    }

    private async void OnGitHubButton_Click(object sender, RoutedEventArgs e)
    {
        var uri = new Uri(ExternalUrl.AndroidGitHubRepoUrl);
        await Launcher.LaunchUriAsync(uri);
    }

    private async void OnGetStartedButton_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            Frame.Navigate(typeof(SyncPage));
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"Could not open device setup: {ex}");
            try
            {
                var dialog = new ContentDialog
                {
                    Title = "Setup could not open",
                    Content = "Please close and reopen Frontbit Link, then try again.",
                    CloseButtonText = "OK",
                    XamlRoot = XamlRoot
                };
                await dialog.ShowAsync();
            }
            catch (Exception dialogException)
            {
                Debug.WriteLine($"Could not show setup error dialog: {dialogException}");
            }
        }
    }
}
