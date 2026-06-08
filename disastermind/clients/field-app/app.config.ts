import { ExpoConfig, ConfigContext } from 'expo/config';

/**
 * Expo app config (PRD Step 8/10).
 *
 * BACKEND_URL is configurable via the EXPO_PUBLIC_BACKEND_URL env var so the
 * real terrestrial transport can POST the exact contract JSON at a backend
 * ingest endpoint. The Python backend ships no device-ingest REST route today,
 * so when no URL is set the app falls back to a fully standalone mock transport
 * and the app runs end-to-end in a simulator with no backend.
 */
export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: 'DisasterMind Field',
  slug: 'disastermind-field-app',
  version: '1.0.0',
  orientation: 'portrait',
  userInterfaceStyle: 'automatic',
  splash: {
    resizeMode: 'contain',
    backgroundColor: '#0b1f33',
  },
  assetBundlePatterns: ['**/*'],
  ios: {
    supportsTablet: true,
    bundleIdentifier: 'com.disastermind.fieldapp',
  },
  android: {
    package: 'com.disastermind.fieldapp',
  },
  web: {
    bundler: 'metro',
  },
  extra: {
    // Configurable backend ingest base URL. Empty -> mock transport.
    backendUrl: process.env.EXPO_PUBLIC_BACKEND_URL ?? '',
    // Device identity defaults (overridable in the app).
    defaultTeamId: process.env.EXPO_PUBLIC_TEAM_ID ?? 'NDRF-01',
    defaultAssetType: process.env.EXPO_PUBLIC_ASSET_TYPE ?? 'ndrf_team',
  },
});
