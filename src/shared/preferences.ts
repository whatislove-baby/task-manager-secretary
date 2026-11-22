import { z } from 'zod';
import type { Result } from './contracts';
export const applicationPreferencesSchema=z.strictObject({launchAtLogin:z.boolean(),widgetOnLaunch:z.boolean(),closeBehavior:z.enum(['tray','quit'])});
export type ApplicationPreferences=z.infer<typeof applicationPreferencesSchema>;
export interface PreferencesState extends ApplicationPreferences { startupSupported:boolean }
export interface PreferencesAPI { get():Promise<Result<PreferencesState>>; save(value:ApplicationPreferences):Promise<Result<PreferencesState>> }
export const defaultApplicationPreferences:ApplicationPreferences={launchAtLogin:false,widgetOnLaunch:false,closeBehavior:'tray'};
