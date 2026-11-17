import React from 'react';
import { Layers, ShieldCheck, ShieldAlert, Globe, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { ModelInfo, ProviderHealth } from '../types';
import { Logo } from './Logo';
import { useLanguage } from '../i18n/LanguageContext';

interface HeaderProps {
  onToggleSidebar: () => void;
  isSidebarOpen?: boolean;
  selectedModels: ModelInfo[];
  onOpenModelSelector: () => void;
  activeConversationTitle?: string;
  health: ProviderHealth | null;
  onOpenKeyModal: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  onToggleSidebar,
  isSidebarOpen = true,
  selectedModels,
  onOpenModelSelector,
  activeConversationTitle,
  health,
  onOpenKeyModal,
}) => {
  const { toggleLanguage, t } = useLanguage();
  const isKeyConfigured = health?.api_key_set ?? false;

  return (
    <header className="h-12 border-b border-slate-800/80 bg-space-900/80 backdrop-blur-md px-3 sm:px-4 flex items-center justify-between z-30 shrink-0">
      <div className="flex items-center gap-2.5 min-w-0">
        <button
          onClick={onToggleSidebar}
          className="p-1.5 rounded-lg text-slate-400 hover:text-cyan-300 hover:bg-space-800 transition-colors"
          title={isSidebarOpen ? "Kenar Çubuğunu Daralt" : "Kenar Çubuğunu Genişlet"}
        >
          {isSidebarOpen ? (
            <PanelLeftClose className="w-4 h-4" />
          ) : (
            <PanelLeftOpen className="w-4 h-4 text-cyan-400" />
          )}
        </button>

        <div className="flex items-center gap-2 min-w-0">
          <div className="hidden sm:block">
            <Logo size={22} />
          </div>
          <span className="font-semibold text-xs sm:text-sm text-slate-200 truncate">
            {activeConversationTitle || t.newComparison}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2 sm:gap-2.5">
        {/* Language Toggle Button */}
        <button
          onClick={toggleLanguage}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl bg-space-850 hover:bg-space-800 border border-slate-700/60 text-xs font-semibold text-slate-300 hover:text-cyan-400 transition-all shadow-sm"
          title={t.changeLanguage}
        >
          <Globe className="w-3.5 h-3.5 text-cyan-400" />
          <span className="font-mono tracking-wider">{t.langLabel}</span>
        </button>

        {/* Model Selection Pill */}
        <button
          onClick={onOpenModelSelector}
          className="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-space-850 hover:bg-space-800 border border-slate-700/60 text-xs font-medium text-slate-300 transition-all shadow-sm"
          title="Manage comparison models"
        >
          <Layers className="w-3.5 h-3.5 text-cyan-400" />
          <span>{selectedModels.length} {t.modelsActive}</span>
        </button>

        {/* NVIDIA API Health / Key status button */}
        <button
          onClick={onOpenKeyModal}
          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl border text-xs font-medium transition-all ${
            isKeyConfigured
              ? 'bg-emerald-950/40 text-emerald-300 border-emerald-800/50 hover:bg-emerald-900/40'
              : 'bg-amber-950/40 text-amber-300 border-amber-800/50 hover:bg-amber-900/40'
          }`}
          title="NVIDIA NIM API Key Status"
        >
          {isKeyConfigured ? (
            <>
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
              <span className="hidden md:inline">{t.nvidiaLive}</span>
            </>
          ) : (
            <>
              <ShieldAlert className="w-3.5 h-3.5 text-amber-400" />
              <span className="hidden md:inline">{t.noApiKey}</span>
            </>
          )}
        </button>
      </div>
    </header>
  );
};
