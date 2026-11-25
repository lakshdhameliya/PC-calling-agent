namespace Sefirah.ViewModels.Dialogs;
public partial class ConnectionRequestViewModel : ObservableObject
{
    private string deviceName = string.Empty;
    public string DeviceName
    {
        get => deviceName;
        set => SetProperty(ref deviceName, value);
    }

    private string verificationKey = string.Empty;
    public string VerificationKey
    {
        get => verificationKey;
        set => SetProperty(ref verificationKey, value);
    }

    public ConnectionRequestViewModel(string deviceName, string verificationKey)
    {
        DeviceName = deviceName;
        VerificationKey = verificationKey;
    }
}
