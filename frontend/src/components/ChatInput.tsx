import React, { useState, useRef, useEffect } from 'react';
import { Send, Layers, Sparkles, SlidersHorizontal, Paperclip, FileText, Image as ImageIcon, X, Loader2, Wand2, RotateCcw, Globe } from 'lucide-react';
import { ModelInfo, AttachmentItem } from '../types';
import { api } from '../services/api';
import { useLanguage } from '../i18n/LanguageContext';

interface ChatInputProps {
  onSendMessage: (prompt: string, systemPrompt?: string, attachments?: AttachmentItem[], webSearch?: boolean) => void;
  isLoading: boolean;
  selectedModels: ModelInfo[];
  onOpenModelSelector: () => void;
  onPromptChange?: (prompt: string) => void;
  onAddVisionModel?: () => void;
  onAutoSelectVisionModels?: () => void;
}

export const ChatInput: React.FC<ChatInputProps> = ({
  onSendMessage,
  isLoading,
  selectedModels,
  onOpenModelSelector,
  onPromptChange,
  onAddVisionModel,
  onAutoSelectVisionModels,
}) => {
  const { language, t } = useLanguage();
  const [prompt, setPrompt] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [showSystemPrompt, setShowSystemPrompt] = useState(false);
  const [attachments, setAttachments] = useState<AttachmentItem[]>([]);
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [isEnhancing, setIsEnhancing] = useState(false);
  const [originalPromptBackup, setOriginalPromptBackup] = useState<string | null>(null);
  const [showEnhancedBadge, setShowEnhancedBadge] = useState(false);
  const [enhancedLatency, setEnhancedLatency] = useState<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  }, [prompt]);

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setPrompt(val);
    if (showEnhancedBadge) {
      setShowEnhancedBadge(false);
    }
    if (onPromptChange) {
      onPromptChange(val);
    }
  };

  const handleEnhancePrompt = async () => {
    if (!prompt.trim() || isEnhancing || isLoading) return;
    setIsEnhancing(true);

    try {
      const res = await api.enhancePrompt(prompt.trim(), language);
      if (res && res.enhanced_prompt) {
        setOriginalPromptBackup(prompt);
        setPrompt(res.enhanced_prompt);
        setEnhancedLatency(res.latency_ms);
        setShowEnhancedBadge(true);
        if (onPromptChange) {
          onPromptChange(res.enhanced_prompt);
        }
      }
    } catch (err: any) {
      console.error('Enhance prompt failed:', err);
    } finally {
      setIsEnhancing(false);
    }
  };

  const handleUndoEnhance = () => {
    if (originalPromptBackup) {
      setPrompt(originalPromptBackup);
      if (onPromptChange) {
        onPromptChange(originalPromptBackup);
      }
      setShowEnhancedBadge(false);
      setOriginalPromptBackup(null);
    }
  };

  const handleUploadFiles = async (files: FileList | File[]) => {
    if (!files || files.length === 0) return;
    setIsUploading(true);

    try {
      const uploadPromises = Array.from(files).map((f) => api.uploadFile(f));
      const uploadedItems = await Promise.all(uploadPromises);
      setAttachments((prev) => [...prev, ...uploadedItems]);

      // Automatically switch to Vision models when image or schematic PDF is detected
      const hasVisual = uploadedItems.some(
        (it) => it.file_type === 'image' || (it.page_images && it.page_images.length > 0)
      );
      if (hasVisual && onAutoSelectVisionModels) {
        onAutoSelectVisionModels();
      }
    } catch (err: any) {
      alert(`Dosya yüklenirken hata oluştu: ${err.message}`);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      handleUploadFiles(e.target.files);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleUploadFiles(e.dataTransfer.files);
    }
  };

  const handleRemoveAttachment = (indexToRemove: number) => {
    setAttachments((prev) => prev.filter((_, idx) => idx !== indexToRemove));
  };

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if ((!prompt.trim() && attachments.length === 0) || isLoading || isUploading) return;
    
    // Fallback prompt if user only uploads document/image without text
    const finalPrompt = prompt.trim() || (
      attachments.some((a) => a.file_type === 'image' || (a.page_images && a.page_images.length > 0))
        ? (t.langLabel === 'TR' ? 'Analyze this circuit schematic and calculate output voltage Vo and currents.' : 'Bu devre şemasını ve görseli analiz et, Vo gerilimini ve akımları hesapla.')
        : (t.langLabel === 'TR' ? 'Analyze and summarize the attached document.' : 'Bu ekli belgeyi incele, özetle ve önemli noktalarını çıkar.')
    );

    onSendMessage(
      finalPrompt,
      systemPrompt.trim() ? systemPrompt.trim() : undefined,
      attachments.length > 0 ? attachments : undefined,
      webSearchEnabled
    );

    setPrompt('');
    setAttachments([]);
    setShowEnhancedBadge(false);
    setOriginalPromptBackup(null);
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="w-full max-w-5xl mx-auto px-2 sm:px-4 pb-2">
      {/* Hidden File Input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/*,application/pdf,text/*,.py,.js,.ts,.tsx,.jsx,.json,.csv,.md,.txt,.html,.css,.sql,.sh,.yml,.yaml"
        onChange={handleFileSelect}
        className="hidden"
      />

      {/* Optional System Prompt Bar */}
      {showSystemPrompt && (
        <div className="mb-1.5 p-2 rounded-xl bg-space-900 border border-slate-800 shadow-md">
          <div className="flex items-center justify-between mb-1 px-1">
            <span className="text-[10px] font-medium text-slate-400">
              {t.systemToggle}
            </span>
            <button
              type="button"
              onClick={() => setShowSystemPrompt(false)}
              className="text-[10px] text-slate-500 hover:text-slate-300"
            >
              ✕
            </button>
          </div>
          <input
            type="text"
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder={t.systemPlaceholder}
            className="w-full bg-space-950 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500/60"
          />
        </div>
      )}

      {/* Main Prompt Input Box with Drag & Drop */}
      <form
        onSubmit={handleSubmit}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        className={`relative flex flex-col rounded-xl bg-space-900 border transition-all duration-200 shadow-lg ${
          isDragging
            ? 'border-cyan-400 bg-space-850 ring-2 ring-cyan-500/30'
            : 'border-slate-800 focus-within:border-cyan-500/60'
        }`}
      >
        {/* Model Chips Bar - Compact */}
        <div className="flex items-center justify-between gap-2 px-3 py-1 border-b border-slate-800/60 bg-space-850/40 text-[10px]">
          <div className="flex items-center gap-1 overflow-x-auto custom-scrollbar py-0.5">
            <span className="text-slate-500 font-medium shrink-0">{t.modelsLabel} ({selectedModels.length}):</span>
            {selectedModels.map((m) => (
              <span
                key={m.id}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-space-800 text-slate-300 border border-slate-700/60 font-medium shrink-0 text-[10px]"
              >
                <Sparkles className="w-2.5 h-2.5 text-cyan-400" />
                <span>{m.name}</span>
              </span>
            ))}
          </div>

          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={() => setShowSystemPrompt(!showSystemPrompt)}
              className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-slate-200 px-1.5 py-0.5 rounded hover:bg-space-800 transition-colors"
              title={t.systemToggle}
            >
              <SlidersHorizontal className="w-2.5 h-2.5" />
              <span className="hidden sm:inline">{t.systemBtn}</span>
            </button>

            <button
              type="button"
              onClick={onOpenModelSelector}
              className="flex items-center gap-1 text-[10px] font-medium text-cyan-400 hover:text-cyan-300 px-2 py-0.5 rounded-md bg-cyan-950/60 border border-cyan-800/60 hover:bg-cyan-900/40 transition-colors"
            >
              <Layers className="w-2.5 h-2.5" />
              <span>{t.changeModels}</span>
            </button>
          </div>
        </div>

        {/* Vision Mode Active Indicator */}
        {attachments.some((a) => a.file_type === 'image' || (a.page_images && a.page_images.length > 0)) && (
          <div className="flex items-center justify-between gap-2 px-3 py-1 bg-emerald-950/60 border-b border-emerald-800/50 text-[10px] text-emerald-300 animate-fadeIn">
            <div className="flex items-center gap-1.5 truncate">
              <Sparkles className="w-3 h-3 text-emerald-400 shrink-0" />
              <span className="truncate">{t.visionModeActive}</span>
            </div>
          </div>
        )}

        {/* Vision Model Recommendation Banner for Schematics/Diagrams */}
        {attachments.length > 0 && !selectedModels.some((m) => m.id.includes('vl') || m.id.includes('vision')) && (
          <div className="flex items-center justify-between gap-2 px-3 py-1 bg-cyan-950/40 border-b border-cyan-800/40 text-[10px] text-cyan-200">
            <div className="flex items-center gap-1.5 truncate">
              <Sparkles className="w-3 h-3 text-cyan-400 shrink-0" />
              <span className="truncate">{t.visionRecommend}</span>
            </div>
            {onAddVisionModel && (
              <button
                type="button"
                onClick={onAddVisionModel}
                className="px-2 py-0.5 rounded bg-cyan-500/20 hover:bg-cyan-500/30 border border-cyan-400/40 text-cyan-300 font-medium text-[9px] shrink-0 transition-colors"
              >
                {t.addVisionModel}
              </button>
            )}
          </div>
        )}

        {/* Uploaded Attachments Preview Bar */}
        {(attachments.length > 0 || isUploading) && (
          <div className="flex flex-wrap items-center gap-1.5 p-2 px-3 border-b border-slate-800/60 bg-space-950/50">
            {attachments.map((att, idx) => (
              <div
                key={idx}
                className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-space-800 border border-slate-700/80 text-[11px] text-slate-200 group animate-fadeIn"
              >
                {att.file_type === 'image' || (att.page_images && att.page_images.length > 0) ? (
                  <div className="w-5 h-5 rounded overflow-hidden bg-space-900 border border-slate-700 shrink-0 flex items-center justify-center">
                    {att.data_url ? (
                      <img src={att.data_url} alt={att.filename} className="w-full h-full object-cover" />
                    ) : (
                      <ImageIcon className="w-3 h-3 text-emerald-400" />
                    )}
                  </div>
                ) : (
                  <FileText className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
                )}
                <div className="flex flex-col max-w-[140px] truncate">
                  <span className="truncate font-medium text-[10px] text-slate-200" title={att.filename}>
                    {att.filename}
                  </span>
                  <span className="text-[8px] text-slate-400">
                    {roundSize(att.size)} {att.page_count ? `• ${att.page_count} sayfa` : ''}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => handleRemoveAttachment(idx)}
                  className="text-slate-400 hover:text-rose-400 p-0.5 rounded transition-colors"
                  title="Kaldır"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            ))}

            {isUploading && (
              <div className="flex items-center gap-1.5 text-[11px] text-cyan-400 px-2 py-0.5 rounded bg-cyan-950/40 border border-cyan-800/50 animate-pulse">
                <Loader2 className="w-3 h-3 animate-spin" />
                <span>{t.uploadingHint}</span>
              </div>
            )}
          </div>
        )}

        {/* Enhanced Prompt Notification Banner */}
        {showEnhancedBadge && (
          <div className="flex items-center justify-between gap-2 px-3 py-1 bg-gradient-to-r from-purple-950/70 via-indigo-950/50 to-space-900 border-b border-purple-800/40 text-[10px] text-purple-200 animate-fadeIn">
            <div className="flex items-center gap-1.5 truncate">
              <Sparkles className="w-3 h-3 text-purple-400 shrink-0" />
              <span>{t.enhancedNotice} {enhancedLatency ? `(~${(enhancedLatency / 1000).toFixed(1)}s)` : ''}</span>
            </div>
            {originalPromptBackup && (
              <button
                type="button"
                onClick={handleUndoEnhance}
                className="flex items-center gap-1 px-2 py-0.5 rounded-md bg-purple-500/20 hover:bg-purple-500/30 border border-purple-400/40 text-purple-300 font-medium text-[10px] shrink-0 transition-colors"
                title="Orijinal prompta geri dön"
              >
                <RotateCcw className="w-2.5 h-2.5" />
                <span>{t.undoEnhance}</span>
              </button>
            )}
          </div>
        )}

        {/* Web Search Active Banner */}
        {webSearchEnabled && (
          <div className="flex items-center justify-between gap-2 px-3 py-1 bg-blue-950/60 border-b border-blue-800/40 text-[10px] text-blue-200 animate-fadeIn">
            <div className="flex items-center gap-1.5 truncate">
              <Globe className="w-3 h-3 text-blue-400 shrink-0" />
              <span>{t.webSearchActive}</span>
            </div>
            <button
              type="button"
              onClick={() => setWebSearchEnabled(false)}
              className="text-[9px] text-blue-400 hover:text-blue-200"
            >
              ✕
            </button>
          </div>
        )}

        {/* Text Input Area & Action Buttons - Streamlined & Compact */}
        <div className="flex items-center gap-1.5 px-2.5 py-1.5">
          {/* File Upload Trigger Button */}
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isLoading || isUploading}
            className="flex items-center justify-center w-8 h-8 rounded-lg bg-space-850 hover:bg-space-800 text-slate-300 hover:text-cyan-400 border border-slate-700/60 hover:border-cyan-500/50 transition-all shrink-0 active:scale-95 disabled:opacity-50"
            title="Belge veya devre fotoğrafı yükle (PDF, Resim, Kod, Metin)"
          >
            <Paperclip className="w-3.5 h-3.5" />
          </button>

          {/* Live Web Search Toggle Button */}
          <button
            type="button"
            onClick={() => setWebSearchEnabled(!webSearchEnabled)}
            className={`flex items-center justify-center w-8 h-8 rounded-lg border transition-all shrink-0 active:scale-95 ${
              webSearchEnabled
                ? 'bg-blue-600/90 text-white border-blue-400 shadow-sm shadow-blue-950/50 ring-1 ring-blue-400/50'
                : 'bg-space-850 hover:bg-space-800 text-slate-300 hover:text-blue-400 border-slate-700/60 hover:border-blue-500/50'
            }`}
            title={webSearchEnabled ? 'Canlı Web Arama Açık (Kapatmak için tıkla)' : 'Canlı Web Arama (Son ilanlar, haberler ve gerçek bağlantılar)'}
          >
            <Globe className="w-3.5 h-3.5" />
          </button>

          <textarea
            ref={textareaRef}
            value={prompt}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder={
              attachments.length > 0
                ? t.inputPlaceholderDocs
                : t.inputPlaceholder
            }
            rows={1}
            disabled={isLoading}
            className="flex-1 bg-transparent text-xs sm:text-sm text-slate-100 placeholder-slate-500 resize-none focus:outline-none min-h-[34px] max-h-[140px] py-1.5 px-2 leading-relaxed custom-scrollbar"
          />

          {/* Magic Prompt Enhancer Button */}
          {prompt.trim().length > 3 && (
            <button
              type="button"
              onClick={handleEnhancePrompt}
              disabled={isEnhancing || isLoading}
              className={`flex items-center gap-1 px-2.5 h-8 rounded-lg text-xs font-medium transition-all shrink-0 active:scale-95 border ${
                isEnhancing
                  ? 'bg-purple-950/80 text-purple-300 border-purple-700/60 animate-pulse'
                  : 'bg-purple-950/50 hover:bg-purple-900/60 text-purple-300 hover:text-purple-100 border-purple-700/50 hover:border-purple-500/70 shadow-sm'
              }`}
              title={t.enhancePromptBtn}
            >
              {isEnhancing ? (
                <>
                  <Loader2 className="w-3 h-3 animate-spin text-purple-400" />
                  <span className="hidden md:inline text-[10px]">{t.enhancingPrompt}</span>
                </>
              ) : (
                <>
                  <Wand2 className="w-3 h-3 text-purple-400" />
                  <span className="hidden sm:inline text-[10px] font-semibold">{t.enhancePromptBtn}</span>
                </>
              )}
            </button>
          )}

          <button
            type="submit"
            disabled={(!prompt.trim() && attachments.length === 0) || isLoading || isUploading}
            className={`flex items-center justify-center w-8 h-8 rounded-lg transition-all shrink-0 ${
              (!prompt.trim() && attachments.length === 0) || isLoading || isUploading
                ? 'bg-space-800 text-slate-600 cursor-not-allowed border border-slate-700/40'
                : 'bg-cyan-500 hover:bg-cyan-400 text-space-950 shadow-md shadow-cyan-950/40 active:scale-95'
            }`}
            title={t.sendBtn}
          >
            <Send className="w-3.5 h-3.5 stroke-[2.5]" />
          </button>
        </div>

        {/* Minimal Footnote */}
        <div className="flex items-center justify-between px-3 pb-1 text-[9px] text-slate-500/80">
          <span>{t.dropHint}</span>
          <span className="hidden sm:inline">{t.footerInfo}</span>
        </div>
      </form>
    </div>
  );
};

function roundSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
