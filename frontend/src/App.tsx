import React, { useState, useEffect, useCallback, useRef } from 'react';
import { api } from './services/api';
import {
  ModelInfo,
  ConversationSummary,
  ConversationDetail,
  TurnDetail,
  ModelResult,
  RouteResponse,
  ProviderHealth,
} from './types';
import { Header } from './components/Header';
import { Sidebar } from './components/Sidebar';
import { ChatInput } from './components/ChatInput';
import { ComparisonGrid } from './components/ComparisonGrid';
import { EmptyState } from './components/EmptyState';
import { ModelSelector } from './components/ModelSelector';
import { ApiKeyModal } from './components/ApiKeyModal';
import { RoutingBadge } from './components/RoutingBadge';

export const App: React.FC = () => {
  // State
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selectedModelIds, setSelectedModelIds] = useState<string[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [activeConversation, setActiveConversation] = useState<ConversationDetail | null>(null);
  
  // In-memory turns for active view
  const [turns, setTurns] = useState<TurnDetail[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [retryingModelKeys, setRetryingModelKeys] = useState<Set<string>>(new Set());

  // Routing State
  const [routeInfo, setRouteInfo] = useState<RouteResponse | null>(null);
  const [isAutoRouteEnabled, setIsAutoRouteEnabled] = useState<boolean>(false);

  // Health & UI Modals
  const [health, setHealth] = useState<ProviderHealth | null>(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState<boolean>(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('multiverse_sidebar_open');
      if (saved !== null) return saved === 'true';
      return window.innerWidth >= 1280; // default open on wide desktop screens
    }
    return true;
  });

  const handleToggleSidebar = () => {
    setIsSidebarOpen((prev) => {
      const next = !prev;
      localStorage.setItem('multiverse_sidebar_open', String(next));
      return next;
    });
  };

  const [isModelSelectorOpen, setIsModelSelectorOpen] = useState<boolean>(false);
  const [isKeyModalOpen, setIsKeyModalOpen] = useState<boolean>(false);

  // Scroll ref
  const scrollEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    scrollEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  // Initial load
  useEffect(() => {
    loadModels();
    loadConversations();
    checkHealth();
  }, []);

  const loadModels = async () => {
    try {
      const res = await api.getModels();
      setModels(res.models);
      if (res.default_models && res.default_models.length > 0) {
        setSelectedModelIds(res.default_models.slice(0, 3));
      } else {
        setSelectedModelIds(res.models.slice(0, 3).map((m) => m.id));
      }
    } catch (err) {
      console.error('Failed to load models:', err);
    }
  };

  const loadConversations = async () => {
    try {
      const list = await api.getConversations();
      setConversations(list);
    } catch (err) {
      console.error('Failed to load conversations:', err);
    }
  };

  const checkHealth = async () => {
    try {
      const h = await api.getHealth();
      setHealth(h);
    } catch (err) {
      console.error('Failed to check health:', err);
    }
  };

  // Switch conversation
  const handleSelectConversation = async (id: string) => {
    try {
      setActiveConversationId(id);
      setIsLoading(true);
      const detail = await api.getConversation(id);
      setActiveConversation(detail);
      setTurns(detail.turns);
    } catch (err) {
      console.error('Failed to load conversation details:', err);
    } finally {
      setIsLoading(false);
    }
  };

  // Start new comparison
  const handleNewConversation = () => {
    setActiveConversationId(null);
    setActiveConversation(null);
    setTurns([]);
    setRouteInfo(null);
  };

  // Delete conversation
  const handleDeleteConversation = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await api.deleteConversation(id);
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (activeConversationId === id) {
        handleNewConversation();
      }
    } catch (err) {
      console.error('Failed to delete conversation:', err);
    }
  };

  // Toggle model selection
  const handleToggleModel = (modelId: string) => {
    setSelectedModelIds((prev) => {
      if (prev.includes(modelId)) {
        if (prev.length <= 1) return prev; // Keep at least one
        return prev.filter((id) => id !== modelId);
      } else {
        if (prev.length >= 6) return prev; // Limit max to 6
        return [...prev, modelId];
      }
    });
  };

  const handleSelectPreset = (modelIds: string[]) => {
    setSelectedModelIds(modelIds);
  };

  // Automatic routing query debounced on prompt change
  const handlePromptChange = useCallback(
    async (text: string) => {
      if (!text.trim() || text.length < 8) {
        setRouteInfo(null);
        return;
      }
      try {
        const route = await api.routePrompt(text);
        setRouteInfo(route);

        // If auto-route mode is enabled, dynamically target recommended models
        if (isAutoRouteEnabled && route.recommended_model_ids.length > 0) {
          setSelectedModelIds(route.recommended_model_ids);
        }
      } catch (err) {
        console.error('Route evaluation error:', err);
      }
    },
    [isAutoRouteEnabled]
  );

  // Send message for multi-model comparison
  const handleSendMessage = async (prompt: string, systemPrompt?: string, attachments?: any[], webSearch?: boolean) => {
    // Check if attachments contains visual graphics (photos, schematics, rendered PDF pages)
    const hasVisualAttachments = attachments?.some(
      (it) => it.file_type === 'image' || (it.page_images && it.page_images.length > 0)
    );

    let activeModelIds = selectedModelIds;

    // Strict Vision Enforcement: If visual schematics or images are attached,
    // exclusively route to Vision-capable models so text-only models do not hallucinate or apologize
    if (hasVisualAttachments) {
      const visionModelIds = models
        .filter((m) => m.id.includes('vl') || m.id.includes('vision') || m.tags.includes('vision'))
        .map((m) => m.id);

      const filteredVision = activeModelIds.filter((mid) => visionModelIds.includes(mid));
      if (filteredVision.length > 0) {
        activeModelIds = filteredVision;
      } else {
        activeModelIds = visionModelIds.length > 0 ? visionModelIds : [
          'nvidia/nemotron-3.5-super-vl-120b-a12b',
          'meta/llama-3.2-90b-vision-instruct'
        ];
      }
      setSelectedModelIds(activeModelIds);
    } else if (isAutoRouteEnabled && routeInfo && routeInfo.recommended_model_ids.length > 0) {
      activeModelIds = routeInfo.recommended_model_ids;
      setSelectedModelIds(activeModelIds);
    }

    const tempTurnId = 'temp-' + Date.now();
    const loadingResults: ModelResult[] = activeModelIds.map((mid) => {
      const m = models.find((mod) => mod.id === mid);
      return {
        model_id: mid,
        model_name: m?.name || mid,
        provider: m?.provider || 'NVIDIA NIM',
        response_text: null,
        response_time_ms: null,
        status: 'loading',
        error_message: null,
      };
    });

    const optimisticTurn: TurnDetail = {
      id: tempTurnId,
      prompt,
      system_prompt: systemPrompt || null,
      created_at: new Date().toISOString(),
      responses: loadingResults,
      attachments: attachments || undefined,
    };

    setTurns((prev) => [...prev, optimisticTurn]);
    setIsLoading(true);
    setTimeout(scrollToBottom, 50);

    try {
      const response = await api.compare({
        prompt,
        model_ids: activeModelIds,
        conversation_id: activeConversationId || undefined,
        system_prompt: systemPrompt,
        attachments: attachments || undefined,
        web_search: webSearch,
      });

      // Update turn with actual results
      const completedTurn: TurnDetail = {
        id: response.turn_id,
        prompt: response.prompt,
        system_prompt: systemPrompt || null,
        created_at: new Date().toISOString(),
        responses: response.results,
        attachments: attachments || undefined,
        web_search_results: response.web_search_results,
      };

      setTurns((prev) =>
        prev.map((t) => (t.id === tempTurnId ? completedTurn : t))
      );

      // If this was a new conversation, set active ID and refresh history
      if (!activeConversationId) {
        setActiveConversationId(response.conversation_id);
      }
      loadConversations();
    } catch (err: any) {
      // If complete network/request error occurred
      const failedResults: ModelResult[] = activeModelIds.map((mid) => {
        const m = models.find((mod) => mod.id === mid);
        return {
          model_id: mid,
          model_name: m?.name || mid,
          provider: m?.provider || 'NVIDIA NIM',
          response_text: null,
          response_time_ms: null,
          status: 'error',
          error_message: err.message || 'Comparison request failed',
        };
      });

      setTurns((prev) =>
        prev.map((t) =>
          t.id === tempTurnId ? { ...t, responses: failedResults } : t
        )
      );
    } finally {
      setIsLoading(false);
      setTimeout(scrollToBottom, 100);
    }
  };

  // Retry individual model
  const handleRetryModel = async (
    turnId: string,
    modelId: string,
    prompt: string,
    systemPrompt?: string
  ) => {
    if (!activeConversationId) return;

    const retryKey = `${turnId}-${modelId}`;
    setRetryingModelKeys((prev) => new Set(prev).add(retryKey));

    try {
      const updatedResult = await api.retry({
        conversation_id: activeConversationId,
        turn_id: turnId,
        model_id: modelId,
        prompt,
        system_prompt: systemPrompt,
      });

      // Update state for this specific card
      setTurns((prev) =>
        prev.map((turn) => {
          if (turn.id !== turnId) return turn;
          return {
            ...turn,
            responses: turn.responses.map((r) =>
              r.model_id === modelId ? updatedResult : r
            ),
          };
        })
      );
    } catch (err: any) {
      console.error('Failed to retry model:', err);
    } finally {
      setRetryingModelKeys((prev) => {
        const next = new Set(prev);
        next.delete(retryKey);
        return next;
      });
    }
  };

  const selectedModelObjects = models.filter((m) =>
    selectedModelIds.includes(m.id)
  );

  const handleAutoSelectVisionModels = () => {
    const visionModels = models.filter(
      (m) => m.id.includes('vl') || m.id.includes('vision') || m.tags.includes('vision')
    );
    if (visionModels.length > 0) {
      setSelectedModelIds(visionModels.map((m) => m.id));
    }
  };

  const handleAddVisionModel = () => {
    const visionModel = models.find(
      (m) => m.id.includes('vl') || m.id.includes('vision') || m.tags.includes('vision')
    );
    if (visionModel && !selectedModelIds.includes(visionModel.id)) {
      setSelectedModelIds((prev) => [...prev, visionModel.id]);
    }
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-space-950 text-slate-100">
      {/* Sidebar */}
      <Sidebar
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
        onDeleteConversation={handleDeleteConversation}
        isBackendConnected={health !== null}
      />

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0 h-full overflow-hidden">
        {/* Top Header */}
        <Header
          onToggleSidebar={handleToggleSidebar}
          isSidebarOpen={isSidebarOpen}
          selectedModels={selectedModelObjects}
          onOpenModelSelector={() => setIsModelSelectorOpen(true)}
          activeConversationTitle={activeConversation?.title}
          health={health}
          onOpenKeyModal={() => setIsKeyModalOpen(true)}
        />

        {/* Workspace Chat Area */}
        <main className="flex-1 overflow-y-auto custom-scrollbar flex flex-col">
          {turns.length === 0 ? (
            <EmptyState
              onSelectPrompt={(p) => {
                handleSendMessage(p);
              }}
            />
          ) : (
            <div className="flex-1 pb-2">
              <ComparisonGrid
                turns={turns}
                onRetryModel={handleRetryModel}
                retryingModelKeys={retryingModelKeys}
              />
              <div ref={scrollEndRef} />
            </div>
          )}
        </main>

        {/* Intelligent Routing Assistant Banner - Only shown when active or recommendation exists */}
        {(routeInfo || isAutoRouteEnabled) && (
          <div className="w-full max-w-5xl mx-auto px-4 pb-1">
            <RoutingBadge
              routeInfo={routeInfo}
              onApplyRecommendation={(mIds) => setSelectedModelIds(mIds)}
              isAutoRouteEnabled={isAutoRouteEnabled}
              onToggleAutoRoute={() => setIsAutoRouteEnabled(!isAutoRouteEnabled)}
            />
          </div>
        )}

        {/* Prompt Input Area */}
        <ChatInput
          onSendMessage={handleSendMessage}
          isLoading={isLoading}
          selectedModels={selectedModelObjects}
          onOpenModelSelector={() => setIsModelSelectorOpen(true)}
          onPromptChange={handlePromptChange}
          onAddVisionModel={handleAddVisionModel}
          onAutoSelectVisionModels={handleAutoSelectVisionModels}
        />
      </div>

      {/* Model Registry Selector Modal */}
      <ModelSelector
        isOpen={isModelSelectorOpen}
        onClose={() => setIsModelSelectorOpen(false)}
        models={models}
        selectedModelIds={selectedModelIds}
        onToggleModel={handleToggleModel}
        onSelectPreset={handleSelectPreset}
        maxModels={6}
      />

      {/* API Key / NVIDIA Status Modal */}
      <ApiKeyModal
        isOpen={isKeyModalOpen}
        onClose={() => setIsKeyModalOpen(false)}
        health={health}
        onRefreshHealth={checkHealth}
      />
    </div>
  );
};

export default App;
