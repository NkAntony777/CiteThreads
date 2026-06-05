export { ChatView } from './ChatView';
export type { ChatViewProps } from './ChatView';
export { ChatInput } from './ChatView/ChatInput';
export type { ChatInputProps } from './ChatView/ChatInput';
export { ConversationList } from './ChatView/ConversationList';
export type {
    ConversationSummary,
    ConversationListProps,
} from './ChatView/ConversationList';

export { AISettings } from './AISettings';
export { HistoryPanel } from './HistoryPanel';
export type { HistoryPanelProps } from './HistoryPanel';

// NOTE (2026-06): The components below were the SearchBar /
// WritingAssistant / SmartSearchPanel / AgentChatPanel /
// PaperSearchPanel / DraftGenerator modules. They have been
// replaced by the new ChatGPT-style ChatView flow:
//   - SmartSearchPanel + PaperSearchPanel + SearchBar (replaced
//     by the chat's natural-language input + agent tools)
//   - WritingAssistant + AgentChatPanel + DraftGenerator (replaced
//     by ChatView's MessageBubble rendering + the agent runtime
//     triggering CTDP via a new tool)
//   - ProjectList (replaced by ConversationList inside ChatView;
//     ProjectList is now HistoryPanel as a quick-access drawer)
//
// Their source files remain on disk (untracked / in git history)
// so existing CTDP logic, agent runtime, and crawler code is
// untouched. To restore the old UI, the easiest path is to
// git-restore WritingAssistant/ + SmartSearchPanel/ + AgentChatPanel/
// + PaperSearchPanel/ and wire SearchBar back into App.tsx.
