import type {MailOperationsAPI} from '../shared/mail-operations';
import type {AgentAPI} from '../shared/agent';
import type {MailAutomationAPI} from '../shared/mail-automation';
import type {MailComposeAPI} from '../shared/mail-compose';
import type {MailWorkspaceAPI} from '../shared/mail-workspace';
import type {PreferencesAPI} from '../shared/preferences';
import type {MailAPI} from '../shared/mail';
import type {DesktopAPI} from '../shared/contracts';
import type {WidgetAPI} from '../shared/widget';
declare global{interface Window{skills?:import('../shared/skills').SkillsAPI;learning?:import('../shared/agent').LearningAPI;taskChanges?:import('../shared/agent').TaskChangesAPI;agent?:AgentAPI;mailFiles?:import('../shared/mail-files').MailFilesAPI;mailAutomation?:MailAutomationAPI;mailCompose:MailComposeAPI;mailOperations:MailOperationsAPI;mailWorkspace:MailWorkspaceAPI;preferences?:PreferencesAPI;mail:MailAPI;desktop:DesktopAPI;widget?:WidgetAPI}}
