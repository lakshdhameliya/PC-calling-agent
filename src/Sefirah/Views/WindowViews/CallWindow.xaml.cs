using Microsoft.UI.Windowing;
using Sefirah.UserControls;
using Sefirah.ViewModels;
using Windows.Graphics;
namespace Sefirah.Views.WindowViews;

public sealed partial class CallWindow : Window
{
    private const int WindowWidth = 350;
    private const int SingleCallWindowHeight = 160;
    private const int DualCallWindowHeight = 230;

    public CallWindowViewModel ViewModel { get; }

    public CallWindow()
    {
        ViewModel = new CallWindowViewModel();
        InitializeComponent();
        Content = new CallView(ViewModel);

        Title = "Call window";
        this.SetWindowIcon();

        var overlapped = (AppWindow.Presenter as OverlappedPresenter) ?? OverlappedPresenter.Create();
        overlapped.IsResizable = false;
        overlapped.IsMaximizable = false;
        overlapped.IsMinimizable = true;
        overlapped.IsAlwaysOnTop = true;
        UpdateWindowSize(false);
#if WINDOWS
        ExtendsContentIntoTitleBar = true;
#endif
        Ioc.Default.GetRequiredService<IAppThemeModeService>().ManageAppearance(this);
        Closed += OnClosed;
        Activate();
#if WINDOWS
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);
        Sefirah.Platforms.Windows.Interop.InteropHelpers.SetForegroundWindow(hwnd);
#endif
    }

    private void OnClosed(object sender, WindowEventArgs args)
    {
        Closed -= OnClosed;
        ViewModel.Dispose();
    }

    public void UpdateWindowSize(bool hasSecondarySession)
    {
        var height = hasSecondarySession ? DualCallWindowHeight : SingleCallWindowHeight;
        AppWindow.Resize(new SizeInt32 { Width = WindowWidth, Height = height });

        var displayArea = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary);
        if (displayArea is not null)
        {
            var workArea = displayArea.WorkArea;
            var x = workArea.X + workArea.Width - WindowWidth - 32;
            var y = workArea.Y + workArea.Height - height - 32;
            AppWindow.Move(new PointInt32 { X = x, Y = y });
        }
    }
}
