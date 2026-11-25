using Sefirah.ViewModels.Dialogs;

namespace Sefirah.Dialogs;

public sealed partial class ConnectionRequestDialog : ContentDialog
{
    public ConnectionRequestViewModel ViewModel
    {
        get => (ConnectionRequestViewModel)DataContext;
        private set => DataContext = value;
    }

    public ConnectionRequestDialog(string deviceName, string verificationKey)
    {
        InitializeComponent();
        ViewModel = new ConnectionRequestViewModel(deviceName, verificationKey);
    }

    private void ContentDialog_SecondaryButtonClick(ContentDialog sender, ContentDialogButtonClickEventArgs args)
    {
        base.Hide();
    }
}
