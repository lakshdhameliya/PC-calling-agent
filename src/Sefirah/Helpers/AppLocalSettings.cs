using System.Text.Json;
using Sefirah.Utils;

namespace Sefirah.Helpers;

/// <summary>
/// Reads and writes app settings in both packaged and unpackaged Windows runs.
/// </summary>
public static class AppLocalSettings
{
    private static readonly object Sync = new();
    private static readonly string FallbackPath = Path.Combine(LocalAppPaths.GetLocalFolderPath(), "local-settings.json");

    public static T? Get<T>(string key, T? defaultValue = default)
    {
        try
        {
            var value = ApplicationData.Current.LocalSettings.Values[key];
            if (value is T typedValue)
                return typedValue;
        }
        catch (Exception)
        {
            // ApplicationData.LocalSettings is unavailable without package identity.
        }

        lock (Sync)
        {
            try
            {
                if (!File.Exists(FallbackPath))
                    return defaultValue;
                var values = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(File.ReadAllText(FallbackPath));
                if (values is null || !values.TryGetValue(key, out var jsonValue))
                    return defaultValue;
                return jsonValue.Deserialize<T>() ?? defaultValue;
            }
            catch (Exception)
            {
                return defaultValue;
            }
        }
    }

    public static void Set<T>(string key, T value)
    {
        try
        {
            ApplicationData.Current.LocalSettings.Values[key] = value;
            return;
        }
        catch (Exception)
        {
            // Fall back to a user-local JSON file for unpackaged launches.
        }

        lock (Sync)
        {
            Dictionary<string, JsonElement> values;
            try
            {
                values = File.Exists(FallbackPath)
                    ? JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(File.ReadAllText(FallbackPath)) ?? []
                    : [];
            }
            catch (Exception)
            {
                values = [];
            }

            values[key] = JsonSerializer.SerializeToElement(value);
            File.WriteAllText(FallbackPath, JsonSerializer.Serialize(values));
        }
    }
}
