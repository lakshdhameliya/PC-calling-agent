// Copyright (c) Files Community
// Licensed under the MIT License.

using System.Globalization;
using Sefirah.Data.Items;
using Windows.Globalization;

namespace Sefirah.Helpers;

/// <summary>
/// Provides static helper to manage supported languages in the application.
/// </summary>
public static class AppLanguageHelper
{
	/// <summary>
	/// A constant string representing the default language code.
	/// It is initialized as an empty string.
	/// </summary>
	private static readonly string _defaultCode = string.Empty;

	/// <summary>
	/// A collection of available languages.
	/// </summary>
	public static ObservableCollection<AppLanguageItem> SupportedLanguages { get; }

	/// <summary>
	/// Gets the preferred language.
	/// </summary>
	public static AppLanguageItem PreferredLanguage { get; private set; }

	/// <summary>
	/// Gets the preferred language.
	/// </summary>
	public static bool IsPreferredLanguageRtl
	{
		get
		{
			if (PreferredLanguage.Code is null)
				return false;

			var culture = new CultureInfo(PreferredLanguage.Code);
			return culture.TextInfo.IsRightToLeft;
		}
	}

	/// <summary>
	/// Initializes the <see cref="AppLanguageHelper"/> class.
	/// </summary>
	static AppLanguageHelper()
	{
		// Populate languages from the package manifest where available. Loose
		// unpackaged launches have no manifest identity, so use the app's
		// bundled language set instead of failing while loading Settings.
		var appLanguages = GetManifestLanguages()
			.Append(string.Empty) // Add default language code
			.Select(language => new AppLanguageItem(language))
			.DistinctBy(language => language.Code, StringComparer.OrdinalIgnoreCase)
			.OrderBy(language => language.Code is not "") // Default language on top
			.ThenBy(language => language.Name)
			.ToList();

		// Get the current primary language override.
		var current = new AppLanguageItem(GetPrimaryLanguageOverride());

		// Find the index of the saved language
		var index = appLanguages.IndexOf(appLanguages.FirstOrDefault(dl => dl.Name == current.Name) ?? appLanguages.First());

		// Set the system default language as the first item in the Languages collection
		var systemLanguage = new AppLanguageItem(CultureInfo.CurrentUICulture.Name, systemDefault: true);
		if (appLanguages.Any(lang => string.Equals(lang.Code, systemLanguage.Code, StringComparison.OrdinalIgnoreCase)))
			appLanguages[0] = systemLanguage;
		else
			appLanguages[0] = new("en-US", systemDefault: true);

		// Initialize the list
		SupportedLanguages = new(appLanguages);
		PreferredLanguage = SupportedLanguages[index];
	}

	private static IEnumerable<string> GetManifestLanguages()
	{
		try
		{
			return ApplicationLanguages.ManifestLanguages.ToArray();
		}
		catch (InvalidOperationException)
		{
			return [
				"ar-SA", "cs-CZ", "de-DE", "en-US", "es-ES", "fr-FR", "he-IL", "hi-IN",
				"hu-HU", "id-ID", "it-IT", "ja-JP", "ko-KR", "nl-NL", "pl-PL", "ru-RU",
				"sv-SE", "tr-TR", "vi-VN", "zh-CN"
			];
		}
	}

	private static string GetPrimaryLanguageOverride()
	{
		try
		{
			return ApplicationLanguages.PrimaryLanguageOverride;
		}
		catch (InvalidOperationException)
		{
			return CultureInfo.CurrentUICulture.Name;
		}
	}

	/// <summary>
	/// Attempts to change the preferred language code by index.
	/// </summary>
	/// <param name="index">The index of the new language.</param>
	/// <returns>True if the language was successfully changed; otherwise, false.</returns>
	public static bool TryChange(int index)
	{
		if (index >= SupportedLanguages.Count || PreferredLanguage == SupportedLanguages[index])
			return false;

		PreferredLanguage = SupportedLanguages[index];

		// Update the primary language override
		try
		{
			ApplicationLanguages.PrimaryLanguageOverride = index == 0 ? _defaultCode : PreferredLanguage.Code;
		}
		catch (InvalidOperationException)
		{
			AppLocalSettings.Set("PreferredLanguageCode", index == 0 ? _defaultCode : PreferredLanguage.Code);
		}
		return true;
	}
}
