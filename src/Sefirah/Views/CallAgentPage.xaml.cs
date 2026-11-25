using Sefirah.Services;

namespace Sefirah.Views;

public sealed partial class CallAgentPage : Page
{
    private readonly CallAgentService service = new();

    public CallAgentPage()
    {
        InitializeComponent();
        Loaded += CallAgentPage_Loaded;
    }

    private async void CallAgentPage_Loaded(object sender, RoutedEventArgs e)
    {
        DashboardWebView.Visibility = Visibility.Collapsed;
        FallbackPanel.Visibility = Visibility.Visible;
        StatusText.Text = "Starting the local dashboard…";

        if (await service.StartDashboardServerAsync())
        {
            DashboardWebView.Source = service.GetDashboardUri();
            DashboardWebView.Visibility = Visibility.Visible;
            FallbackPanel.Visibility = Visibility.Collapsed;
        }
        else
        {
            StatusText.Text = "Dashboard server did not start. Check the Python environment and try again.";
        }
    }

    private void StartBot_Click(object sender, RoutedEventArgs e)
    {
        StatusText.Text = service.Start() ? "Bot is starting / running" : "Bot files were not found on this PC";
    }

    private void StopBot_Click(object sender, RoutedEventArgs e)
    {
        service.Stop();
        StatusText.Text = "Bot is stopped";
    }

    private void OpenDashboard_Click(object sender, RoutedEventArgs e) => service.OpenDashboard();
    private void OpenLiveChat_Click(object sender, RoutedEventArgs e) => service.OpenLiveChat();
}
