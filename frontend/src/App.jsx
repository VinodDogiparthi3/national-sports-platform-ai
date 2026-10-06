import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowUpRight,
  ArrowUp,
  Activity,
  BarChart3,
  Bot,
  ChevronDown,
  Clock3,
  FileText,
  Home,
  Menu,
  MessageSquareText,
  MapPin,
  PanelLeftClose,
  PanelLeftOpen,
  PersonStanding,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Trophy,
  TrendingUp,
  UsersRound,
  X,
} from "lucide-react";
import "./Workspace.css";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");
const HISTORY_KEY = "national-sports-platform-chat-history-v1";
const LANGUAGE_KEY = "national-sports-platform-ui-language";
const MAX_SAVED_CHATS = 12;
const sidebarItems = [
  { key: "home", Icon: Home },
  { key: "analytics", Icon: BarChart3 },
  { key: "athletes", Icon: UsersRound },
  { key: "sports", Icon: Trophy },
  { key: "regions", Icon: MapPin },
  { key: "reports", Icon: FileText },
];
const quickInsightIcons = [BarChart3, TrendingUp, UsersRound, MapPin];

const interfaceText = {
  en: {
    brand: "National Sports",
    brandSubtitle: "Platform AI",
    newConversation: "New conversation",
    workspace: "Workspace",
    navigation: { home: "Home", analytics: "Analytics", athletes: "Athletes", sports: "Sports", regions: "Regions", reports: "Reports" },
    navigationUnavailable: "This section is not available yet",
    askData: "Ask the data",
    recentConversations: "Recent conversations",
    searchPlaceholder: "Search history",
    searchLabel: "Search conversations",
    emptyHistory: "Your conversations will appear here.",
    apiConnected: "API connected",
    apiUnavailable: "API unavailable",
    apiChecking: "Checking API",
    apiDescription: "BigQuery assistant",
    context: "National Sports Platform",
    newChat: "New chat",
    welcomeTitle: "What would you like to explore?",
    welcomeBody: "Ask questions about registrations, athletes, sports, federations, regions and participation across the platform.",
    visualLabel: "SPORTS DATA",
    examples: [
      "Explore athlete participation across sports...",
      "Compare registrations across regions...",
      "Show registration growth over time...",
      "Explore athletes by region...",
      "Compare participation across groups...",
    ],
    topicsLabel: "Explore by topic",
    topics: { athletes: "Athletes", registrations: "Registrations", sports: "Sports", federations: "Federations", regions: "Regions", trends: "Trends" },
    topicPlaceholders: {
      athletes: "Ask about athletes and participation...",
      registrations: "Ask about registrations...",
      sports: "Ask about sports...",
      federations: "Ask about federations...",
      regions: "Ask about regions...",
      trends: "Ask about trends over time...",
    },
    suggestions: [
      "How many registrations are there for each sport?",
      "Show registration trends by year.",
      "Which sports have the most active athletes?",
      "Compare registrations across regions.",
    ],
    quickInsightsTitle: "Quick Insights",
    quickInsightsBody: "Start with a popular question or ask your own.",
    quickInsightCategories: ["Registrations", "Trends", "Athletes", "Regions"],
    you: "You",
    assistant: "Sports Platform AI",
    thinking: "Checking the platform data…",
    processingSteps: ["Understanding your question…", "Analyzing sports data…", "Preparing your answer…"],
    sql: "View generated SQL",
    inputPlaceholder: "Ask anything about National Sports Platform data...",
    inputLabel: "Ask a question",
    send: "Send question",
    privacyNote: "Answers are grounded in platform data. Verify high-impact decisions.",
    openMenu: "Open conversation menu",
    closeMenu: "Close conversation menu",
    closeSidebar: "Close sidebar",
    collapseSidebar: "Collapse sidebar",
    expandSidebar: "Expand sidebar",
    language: "Language",
    pageTitle: "National Sports Platform AI",
  },
  ar: {
    brand: "المنصة الوطنية للرياضة",
    brandSubtitle: "المساعد الذكي",
    newConversation: "محادثة جديدة",
    workspace: "مساحة العمل",
    navigation: { home: "الرئيسية", analytics: "التحليلات", athletes: "الرياضيون", sports: "الرياضات", regions: "المناطق", reports: "التقارير" },
    navigationUnavailable: "هذا القسم غير متاح حالياً",
    askData: "اسأل البيانات",
    recentConversations: "المحادثات الأخيرة",
    searchPlaceholder: "ابحث في المحادثات",
    searchLabel: "البحث في المحادثات",
    emptyHistory: "ستظهر محادثاتك هنا.",
    apiConnected: "الخدمة متصلة",
    apiUnavailable: "الخدمة غير متاحة",
    apiChecking: "جارٍ التحقق من الخدمة",
    apiDescription: "مساعد بيانات BigQuery",
    context: "المنصة الوطنية للرياضة",
    newChat: "محادثة جديدة",
    welcomeTitle: "ما الذي ترغب في استكشافه؟",
    welcomeBody: "اسأل عن التسجيلات والرياضيين والرياضات والاتحادات والمناطق والمشاركة عبر المنصة.",
    visualLabel: "بيانات الرياضة",
    examples: [
      "استكشف مشاركة الرياضيين عبر الرياضات...",
      "قارن التسجيلات بين المناطق...",
      "اعرض نمو التسجيلات بمرور الوقت...",
      "استكشف الرياضيين حسب المنطقة...",
      "قارن المشاركة بين الفئات...",
    ],
    topicsLabel: "استكشف حسب الموضوع",
    topics: { athletes: "الرياضيون", registrations: "التسجيلات", sports: "الرياضات", federations: "الاتحادات", regions: "المناطق", trends: "الاتجاهات" },
    topicPlaceholders: {
      athletes: "اسأل عن الرياضيين والمشاركة...",
      registrations: "اسأل عن التسجيلات...",
      sports: "اسأل عن الرياضات...",
      federations: "اسأل عن الاتحادات...",
      regions: "اسأل عن المناطق...",
      trends: "اسأل عن الاتجاهات بمرور الوقت...",
    },
    suggestions: [
      "كم عدد التسجيلات لكل رياضة؟",
      "اعرض اتجاهات التسجيل حسب السنة.",
      "ما الرياضات التي تضم أكبر عدد من الرياضيين النشطين؟",
      "قارن التسجيلات بين المناطق.",
    ],
    quickInsightsTitle: "رؤى سريعة",
    quickInsightsBody: "ابدأ بسؤال شائع أو اكتب سؤالك الخاص.",
    quickInsightCategories: ["التسجيلات", "الاتجاهات", "الرياضيون", "المناطق"],
    you: "أنت",
    assistant: "مساعد المنصة الرياضية",
    thinking: "جارٍ التحقق من بيانات المنصة…",
    processingSteps: ["جارٍ فهم سؤالك…", "جارٍ تحليل البيانات الرياضية…", "جارٍ إعداد الإجابة…"],
    sql: "عرض SQL المُنشأ",
    inputPlaceholder: "اسأل عن أي بيانات في المنصة الوطنية للرياضة...",
    inputLabel: "اكتب سؤالاً",
    send: "إرسال السؤال",
    privacyNote: "الإجابات مستندة إلى بيانات المنصة. تحقّق من القرارات المهمة.",
    openMenu: "فتح قائمة المحادثات",
    closeMenu: "إغلاق قائمة المحادثات",
    closeSidebar: "إغلاق القائمة الجانبية",
    collapseSidebar: "طي القائمة الجانبية",
    expandSidebar: "توسيع القائمة الجانبية",
    language: "اللغة",
    pageTitle: "المنصة الوطنية للرياضة | المساعد الذكي",
  },
};

function readLanguage() {
  try {
    return localStorage.getItem(LANGUAGE_KEY) === "en" ? "en" : "ar";
  } catch {
    return "ar";
  }
}

function readChatHistory() {
  try {
    const history = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    return Array.isArray(history)
      ? history.filter((chat) => chat && Array.isArray(chat.messages)).slice(0, MAX_SAVED_CHATS)
      : [];
  } catch {
    return [];
  }
}

function saveChat(history, chat) {
  return [chat, ...history.filter((item) => item.id !== chat.id)].slice(0, MAX_SAVED_CHATS);
}

function QuestionComposer({ input, inputRef, loading, onChange, onFocus, onBlur, onKeyDown, onSubmit, placeholder, ui, landing = false }) {
  return (
    <div className={`composer-wrap ${landing ? "composer-wrap-landing" : "composer-wrap-sticky"}`}>
      <form className="composer" onSubmit={onSubmit}>
        <Sparkles className="composer-spark" size={17} aria-hidden="true" />
        <textarea
          ref={inputRef}
          value={input}
          onChange={onChange}
          onFocus={onFocus}
          onBlur={onBlur}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          rows={1}
          dir="auto"
          disabled={loading}
          aria-label={ui.inputLabel}
        />
        <button type="submit" disabled={!input.trim() || loading} aria-label={ui.send} title={ui.send}>
          <ArrowUp size={19} />
        </button>
      </form>
      <p className="composer-note"><ShieldCheck size={12} /> {ui.privacyNote}</p>
    </div>
  );
}

function QuickInsightCard({ question, category, index, onSelect }) {
  const Icon = quickInsightIcons[index] || BarChart3;
  return (
    <button className={`quick-insight accent-${index + 1}`} type="button" onClick={onSelect}>
      <span className="insight-icon"><Icon size={18} /></span>
      <span className="insight-copy">
        <span className="insight-category">{category}</span>
        <strong>{question}</strong>
      </span>
      <span className={`insight-mini mini-${index + 1}`} aria-hidden="true">
        {index === 0 && <span className="mini-bars"><i /><i /><i /><i /></span>}
        {index === 1 && <svg viewBox="0 0 58 34" focusable="false"><path d="M2 28 L15 21 L25 24 L38 10 L55 5" /></svg>}
        {index === 2 && <span className="mini-people"><i /><i /><i /></span>}
        {index === 3 && <MapPin size={25} />}
      </span>
      <ArrowUpRight className="insight-arrow" size={16} />
    </button>
  );
}

function App() {
  const [language, setLanguage] = useState(readLanguage);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState([]);
  const [savedChats, setSavedChats] = useState(readChatHistory);
  const [activeChatId, setActiveChatId] = useState(null);
  const [historySearch, setHistorySearch] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [apiStatus, setApiStatus] = useState("checking");
  const [activeTopic, setActiveTopic] = useState(null);
  const [composerFocused, setComposerFocused] = useState(false);
  const [exampleIndex, setExampleIndex] = useState(0);
  const [processingStep, setProcessingStep] = useState(0);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const ui = interfaceText[language];
  const isArabic = language === "ar";

  useEffect(() => {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(savedChats));
  }, [savedChats]);

  useEffect(() => {
    localStorage.setItem(LANGUAGE_KEY, language);
    document.documentElement.lang = language;
    document.documentElement.dir = isArabic ? "rtl" : "ltr";
    document.title = ui.pageTitle;
  }, [isArabic, language, ui.pageTitle]);

  useEffect(() => {
    let active = true;
    fetch(`${API_BASE_URL}/health`)
      .then((response) => {
        if (!response.ok) throw new Error("API unavailable");
        return response.json();
      })
      .then(() => active && setApiStatus("connected"))
      .catch(() => active && setApiStatus("offline"));
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (input || composerFocused || activeTopic) return undefined;
    const timer = window.setInterval(() => {
      setExampleIndex((index) => (index + 1) % ui.examples.length);
    }, 4600);
    return () => window.clearInterval(timer);
  }, [activeTopic, composerFocused, input, language, ui.examples.length]);

  useEffect(() => {
    if (!loading) return undefined;
    const timer = window.setInterval(() => {
      setProcessingStep((step) => (step + 1) % ui.processingSteps.length);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [language, loading, ui.processingSteps.length]);

  const filteredChats = savedChats.filter((chat) =>
    chat.title.toLowerCase().includes(historySearch.toLowerCase()),
  );

  const startNewChat = () => {
    if (loading) return;
    setMessages([]);
    setActiveChatId(null);
    setActiveTopic(null);
    setInput("");
    setSidebarOpen(false);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  const toggleSidebar = () => {
    if (window.matchMedia("(max-width: 820px)").matches) {
      setSidebarOpen((open) => !open);
    } else {
      setSidebarCollapsed((collapsed) => !collapsed);
    }
  };

  const selectNavigationItem = () => startNewChat();

  const openChat = (chat) => {
    if (loading) return;
    setActiveChatId(chat.id);
    setActiveTopic(null);
    setMessages(chat.messages);
    setSidebarOpen(false);
  };

  const sendQuestion = async (rawQuestion = input) => {
    const question = rawQuestion.trim();
    if (!question || loading) return;

    const chatId = activeChatId || crypto.randomUUID();
    const existingChat = savedChats.find((chat) => chat.id === chatId);
    const title = existingChat?.title || question.slice(0, 52);
    const userMessage = {
      id: crypto.randomUUID(),
      role: "user",
      text: question,
    };
    const userMessages = [...messages, userMessage];

    setActiveChatId(chatId);
    setMessages(userMessages);
    setSavedChats((history) => saveChat(history, {
      id: chatId,
      title,
      messages: userMessages,
      updatedAt: Date.now(),
    }));
    setInput("");
    setActiveTopic(null);
    setSidebarOpen(false);
    if (inputRef.current) inputRef.current.style.height = "auto";
    setProcessingStep(0);
    setLoading(true);

    try {
      const response = await fetch(`${API_BASE_URL}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(data.detail || `Request failed (${response.status})`);
      }

      const assistantMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        text: data.answer,
        sql: data.sql,
      };
      const updatedMessages = [...userMessages, assistantMessage];
      setMessages(updatedMessages);
      setSavedChats((history) => saveChat(history, {
        id: chatId,
        title,
        messages: updatedMessages,
        updatedAt: Date.now(),
      }));
    } catch (error) {
      const assistantMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        text: error.message || "Sorry, I was unable to process your request.",
        error: true,
      };
      const updatedMessages = [...userMessages, assistantMessage];
      setMessages(updatedMessages);
      setSavedChats((history) => saveChat(history, {
        id: chatId,
        title,
        messages: updatedMessages,
        updatedAt: Date.now(),
      }));
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      sendQuestion();
    }
  };

  const handleInputChange = (event) => {
    setInput(event.target.value);
    if (event.target.value) setActiveTopic(null);
    event.target.style.height = "auto";
    event.target.style.height = `${Math.min(event.target.scrollHeight, 128)}px`;
  };

  const selectTopic = (topic) => {
    setActiveTopic(topic);
    setSidebarOpen(false);
    inputRef.current?.focus();
  };

  const queryPlaceholder = activeTopic
    ? ui.topicPlaceholders[activeTopic]
    : input || composerFocused
      ? ui.inputPlaceholder
      : ui.examples[exampleIndex];

  const submitForm = (event) => {
    event.preventDefault();
    sendQuestion();
  };

  return (
    <div className={`assistant-app ${sidebarCollapsed ? "sidebar-collapsed" : ""}`} dir={isArabic ? "rtl" : "ltr"}>
      {sidebarOpen && (
        <button
          className="sidebar-scrim"
          aria-label={ui.closeMenu}
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside className={`sidebar ${sidebarOpen ? "is-open" : ""}`}>
        <div className="sidebar-brand">
          <div className="brand-mark"><Sparkles size={18} /></div>
          <div className="brand-copy">
            <strong>{ui.brand}</strong>
            <span>{ui.brandSubtitle}</span>
          </div>
          <button
            className="sidebar-collapse"
            onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
            aria-label={sidebarCollapsed ? ui.expandSidebar : ui.collapseSidebar}
            title={sidebarCollapsed ? ui.expandSidebar : ui.collapseSidebar}
          >
            {sidebarCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </button>
          <button
            className="sidebar-close"
            onClick={() => setSidebarOpen(false)}
            aria-label={ui.closeSidebar}
          >
            <X size={18} />
          </button>
        </div>

        <button className="new-chat-button" onClick={startNewChat} disabled={loading} aria-label={ui.newConversation} title={ui.newConversation}>
          <Plus size={17} />
          <span>{ui.newConversation}</span>
        </button>

        <nav className="sidebar-nav" aria-label={ui.workspace}>
          <span className="sidebar-label">{ui.workspace}</span>
          {sidebarItems.map(({ key, Icon }) => (
            <button
              key={key}
              className={`nav-item ${key === "home" ? "active" : ""}`}
              onClick={() => selectNavigationItem(key)}
              disabled={key !== "home" || loading}
              title={key === "home" ? ui.navigation[key] : ui.navigationUnavailable}
              aria-current={key === "home" ? "page" : undefined}
            >
              <Icon size={16} />
              <span>{ui.navigation[key]}</span>
            </button>
          ))}
        </nav>

        <section className="history-panel" aria-label={ui.recentConversations}>
          <div className="history-heading">
            <span>{ui.recentConversations}</span>
            <Clock3 size={14} />
          </div>
          <label className="history-search">
            <Search size={14} />
            <input
              type="search"
              value={historySearch}
              onChange={(event) => setHistorySearch(event.target.value)}
              placeholder={ui.searchPlaceholder}
              aria-label={ui.searchLabel}
            />
          </label>
          <div className="history-list">
            {filteredChats.length === 0 ? (
              <p className="history-empty">{ui.emptyHistory}</p>
            ) : filteredChats.map((chat) => (
              <button
                key={chat.id}
                className={`history-item ${chat.id === activeChatId ? "selected" : ""}`}
                onClick={() => openChat(chat)}
                title={chat.title}
              >
                <MessageSquareText size={14} />
                <span>{chat.title}</span>
              </button>
            ))}
          </div>
        </section>

        <div className="sidebar-status">
          <span className={`status-dot ${apiStatus}`} />
          <div>
            <strong>{apiStatus === "connected" ? ui.apiConnected : apiStatus === "offline" ? ui.apiUnavailable : ui.apiChecking}</strong>
            <span>{ui.apiDescription}</span>
          </div>
          <Activity size={15} />
        </div>
      </aside>

      <div className="workspace-main">
        <header className="workspace-header">
          <div className="header-context">
            <button
              className="menu-button"
              onClick={toggleSidebar}
              aria-label={ui.openMenu}
              aria-expanded={window.matchMedia("(max-width: 820px)").matches ? sidebarOpen : !sidebarCollapsed}
            >
              <Menu size={19} />
            </button>
            <div>
              <span>{ui.context}</span>
              <strong>{activeChatId ? savedChats.find((chat) => chat.id === activeChatId)?.title || ui.askData : ui.askData}</strong>
            </div>
          </div>
          <div className="header-actions">
            <div className="language-switch" role="group" aria-label={ui.language}>
              <button
                className={language === "en" ? "selected" : ""}
                onClick={() => setLanguage("en")}
                aria-pressed={language === "en"}
              >EN</button>
              <button
                className={language === "ar" ? "selected" : ""}
                onClick={() => setLanguage("ar")}
                aria-pressed={language === "ar"}
              >عربي</button>
            </div>
            <button className="header-new-chat" onClick={startNewChat} disabled={loading}>
              <Plus size={15} />
              <span>{ui.newChat}</span>
            </button>
          </div>
        </header>

        <main className={`chat-workspace ${messages.length === 0 ? "is-empty" : "has-messages"}`}>
          {messages.length === 0 ? (
            <section className="welcome-panel">
              <div className="welcome-intro">
                <div className="welcome-copy">
                  <span className="welcome-kicker">{ui.brand}</span>
                  <h1>{ui.welcomeTitle}</h1>
                  <p>{ui.welcomeBody}</p>
                </div>
                <div className="sports-data-visual" aria-hidden="true">
                  <div className="visual-heading"><Activity size={17} /><span>{ui.visualLabel}</span></div>
                  <div className="visual-chart">
                    <div className="visual-bars"><i /><i /><i /><i /><i /><i /></div>
                    <div className="visual-athlete"><PersonStanding size={54} strokeWidth={1.35} /></div>
                    <div className="visual-trophy"><Trophy size={20} /></div>
                  </div>
                  <div className="visual-caption"><span /><span /><span /></div>
                </div>
              </div>
              <QuestionComposer
                input={input}
                inputRef={inputRef}
                loading={loading}
                onChange={handleInputChange}
                onFocus={() => setComposerFocused(true)}
                onBlur={() => setComposerFocused(false)}
                onKeyDown={handleKeyDown}
                onSubmit={submitForm}
                placeholder={queryPlaceholder}
                ui={ui}
                landing
              />
              <section className="quick-insights" aria-label={ui.quickInsightsTitle}>
                <div className="quick-insights-heading">
                  <h2>{ui.quickInsightsTitle}</h2>
                  <p>{ui.quickInsightsBody}</p>
                </div>
                <div className="quick-insight-grid">
                  {ui.suggestions.map((question, index) => (
                    <QuickInsightCard
                      key={question}
                      question={question}
                      category={ui.quickInsightCategories[index]}
                      index={index}
                      onSelect={() => sendQuestion(question)}
                    />
                  ))}
                </div>
              </section>
              <section className="topic-section" aria-label={ui.topicsLabel}>
                <p className="topic-label">{ui.topicsLabel}</p>
                <div className="topic-list">
                  {Object.entries(ui.topics).map(([key, label]) => (
                    <button
                      key={key}
                      type="button"
                      className={`topic-chip ${activeTopic === key ? "active" : ""}`}
                      onClick={() => selectTopic(key)}
                      aria-pressed={activeTopic === key}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </section>
            </section>
          ) : (
            <section className="message-list" aria-live="polite">
              {messages.map((message) => (
                <div className={`message-row ${message.role}`} key={message.id}>
                  {message.role === "assistant" && (
                    <div className="assistant-avatar"><Bot size={17} /></div>
                  )}
                  <div className="message-content">
                    <span className="message-author">{message.role === "user" ? ui.you : ui.assistant}</span>
                    <div className={`message-bubble ${message.role} ${message.error ? "has-error" : ""}`} dir="auto">
                      {message.role === "assistant" ? (
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
                      ) : message.text}
                    </div>
                    {message.role === "assistant" && message.sql && (
                      <details className="sql-details">
                        <summary><ChevronDown size={14} /> {ui.sql}</summary>
                        <code>{message.sql}</code>
                      </details>
                    )}
                  </div>
                </div>
              ))}
              {loading && (
                <div className="message-row assistant">
                  <div className="assistant-avatar"><Bot size={17} /></div>
                  <div className="message-content">
                    <span className="message-author">{ui.assistant}</span>
                    <div className="thinking-indicator" role="status">
                      <span /><span /><span />
                      <p>{ui.processingSteps[processingStep]}</p>
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </section>
          )}
        </main>

        {messages.length > 0 && (
          <QuestionComposer
            input={input}
            inputRef={inputRef}
            loading={loading}
            onChange={handleInputChange}
            onFocus={() => setComposerFocused(true)}
            onBlur={() => setComposerFocused(false)}
            onKeyDown={handleKeyDown}
            onSubmit={submitForm}
            placeholder={queryPlaceholder}
            ui={ui}
          />
        )}
      </div>
    </div>
  );
}

export default App;