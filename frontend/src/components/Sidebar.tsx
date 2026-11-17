import React from 'react';
import { Plus, MessageSquare, Trash2, Orbit, PanelLeftClose } from 'lucide-react';
import { ConversationSummary } from '../types';
import { Logo } from './Logo';
import { useLanguage } from '../i18n/LanguageContext';

interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onDeleteConversation: (id: string, e: React.MouseEvent) => void;
  isBackendConnected: boolean;
}

export const Sidebar: React.FC<SidebarProps> = ({
  isOpen,
  onClose,
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
  isBackendConnected,
}) => {
  const { t } = useLanguage();

  return (
    <>
      {/* Mobile backdrop */}
      {isOpen && (
        <div
          onClick={onClose}
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
        />
      )}

      <aside
        className={`fixed lg:static top-0 bottom-0 left-0 z-40 flex flex-col bg-space-900 border-r border-slate-800 transition-all duration-300 ease-in-out shrink-0 overflow-hidden ${
          isOpen
            ? 'w-64 sm:w-72 translate-x-0 opacity-100'
            : '-translate-x-full lg:translate-x-0 lg:w-0 lg:border-r-0 lg:opacity-0 pointer-events-none'
        }`}
      >
        {/* Brand Header */}
        <div className="flex items-center justify-between p-3.5 border-b border-slate-800/80">
          <div className="flex items-center gap-2.5 min-w-0">
            <Logo size={28} />
            <div className="truncate">
              <h1 className="text-xs font-bold tracking-tight text-white flex items-center gap-1.5">
                MULTIVERSE
              </h1>
              <p className="text-[9px] text-slate-400 font-medium tracking-wide truncate">
                One question. Multiple minds.
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-slate-400 hover:text-cyan-300 hover:bg-space-800 transition-colors"
            title="Kenar Çubuğunu Daralt"
          >
            <PanelLeftClose className="w-4 h-4" />
          </button>
        </div>

        {/* New Comparison Button */}
        <div className="p-2.5">
          <button
            onClick={() => {
              onNewConversation();
              // Only close on mobile
              if (window.innerWidth < 1024) onClose();
            }}
            className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-xl text-xs font-semibold bg-cyan-500 hover:bg-cyan-400 text-space-950 transition-all shadow-md shadow-cyan-950/40 hover:shadow-cyan-500/20 active:scale-[0.98]"
          >
            <Plus className="w-3.5 h-3.5 stroke-[2.5]" />
            <span>{t.newComparison}</span>
          </button>
        </div>

        {/* Conversation List */}
        <div className="flex-1 overflow-y-auto px-2.5 py-1.5 space-y-1 custom-scrollbar">
          <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            {t.conversations}
          </div>

          {conversations.length === 0 ? (
            <div className="px-3 py-8 text-center text-xs text-slate-500 space-y-2">
              <Orbit className="w-6 h-6 mx-auto opacity-30 text-slate-400" />
              <p>{t.noChats}</p>
              <p className="text-[10px] text-slate-600">{t.noChatsDesc}</p>
            </div>
          ) : (
            conversations.map((conv) => {
              const isActive = conv.id === activeConversationId;
              return (
                <div
                  key={conv.id}
                  onClick={() => {
                    onSelectConversation(conv.id);
                    if (window.innerWidth < 1024) onClose();
                  }}
                  className={`group relative flex items-center justify-between p-2 rounded-xl cursor-pointer text-xs transition-all ${
                    isActive
                      ? 'bg-space-800 text-cyan-300 font-medium border border-slate-700/80 shadow-sm'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-space-850 border border-transparent'
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0 pr-1.5">
                    <MessageSquare className={`w-3.5 h-3.5 shrink-0 ${isActive ? 'text-cyan-400' : 'text-slate-500'}`} />
                    <span className="truncate text-[11px]">{conv.title}</span>
                  </div>

                  <button
                    onClick={(e) => {
                      if (window.confirm(t.deleteConfirm)) {
                        onDeleteConversation(conv.id, e);
                      }
                    }}
                    className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-rose-950/60 hover:text-rose-400 text-slate-500 transition-all shrink-0"
                    title="Delete"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              );
            })
          )}
        </div>

        {/* Provider Status Footer */}
        <div className="p-2.5 border-t border-slate-800/80 bg-space-950/60 text-[10px]">
          <div className="flex items-center justify-between text-slate-400 px-1 py-0.5">
            <span className="flex items-center gap-1.5">
              <span className={`w-2 h-2 rounded-full ${isBackendConnected ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
              <span className="font-medium">NVIDIA NIM</span>
            </span>
            <span className="text-[9px] font-mono text-slate-500">v1.2.0</span>
          </div>
        </div>
      </aside>
    </>
  );
};
