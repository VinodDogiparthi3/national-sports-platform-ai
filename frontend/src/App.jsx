import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { streamQuestion, translateChatTexts } from "./api";
import {
  getConversationHistory,
  getLatestConversationContext,
  getLatestExportContext,
  mergeSources,
  resolveDownloadUrl,
} from "./exportContext";
import { parseResultCards, parseViewResults, splitResultsMarkdown } from "./resultsDisplay";
import { TIP_ROTATION_MS, nextTipIndex, stepState, waitingTips } from "./waitingTips";
import {
  ArrowUpRight,
  ArrowUp,
  Activity,
  BarChart3,
  Bot,
  Clock3,
  FileText,
  Home,
  Menu,
  MessageSquareText,
  MapPin,
  PersonStanding,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Trophy,
  TrendingUp,
  UsersRound,
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
const processingStatusIndex = { understanding: 0, analyzing: 1, preparing: 2 };

const interfaceText = {
  en: {
    brand: "National Sports",
    brandSubtitle: "Platform AI",
    newConversation: "New Conversation",
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
    apiDescription: "Sports data assistant",
    version: "Version",
    versionUnavailable: "Version unavailable",
    context: "National Sports Platform",
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
    genericError: "Unable to process the request. Please try again.",
    downloadExcel: "Download Excel (.xlsx)",
    processingSteps: ["Understanding your question…", "Analyzing sports data…", "Preparing your answer…"],
    stepLabels: ["Understand", "Analyze", "Prepare"],
    inputPlaceholder: "Ask anything about National Sports Platform data...",
    inputLabel: "Ask a question",
    send: "Send question",
    privacyNote: "Answers are grounded in platform data. Verify high-impact decisions.",
    openMenu: "Open sidebar",
    closeMenu: "Close sidebar",
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
    apiDescription: "مساعد بيانات المنصة الرياضية",
    version: "الإصدار",
    versionUnavailable: "الإصدار غير متاح",
    context: "المنصة الوطنية للرياضة",
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
    genericError: "تعذر معالجة الطلب. يرجى المحاولة مرة أخرى.",
    downloadExcel: "تنزيل Excel (.xlsx)",
    processingSteps: ["جارٍ فهم سؤالك…", "جارٍ تحليل البيانات الرياضية…", "جارٍ إعداد الإجابة…"],
    stepLabels: ["الفهم", "التحليل", "الإعداد"],
    inputPlaceholder: "اسأل عن أي بيانات في المنصة الوطنية للرياضة...",
    inputLabel: "اكتب سؤالاً",
    send: "إرسال السؤال",
    privacyNote: "الإجابات مستندة إلى بيانات المنصة. تحقّق من القرارات المهمة.",
    openMenu: "فتح القائمة الجانبية",
    closeMenu: "إغلاق القائمة الجانبية",
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

async function translateTextsInBatches(texts, targetLanguage) {
  const translations = [];
  for (let index = 0; index < texts.length; index += 50) {
    const batch = texts.slice(index, index + 50);
    translations.push(...await translateChatTexts(batch, targetLanguage));
  }
  return translations;
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

function WaitingExperience({ labels, currentStep, tips }) {
  const [tipIndex, setTipIndex] = useState(0);

  useEffect(() => {
    const interval = window.setInterval(
      () => setTipIndex((index) => nextTipIndex(index, tips.length)),
      TIP_ROTATION_MS,
    );
    return () => window.clearInterval(interval);
  }, [tips.length]);

  return (
    <div className="waiting-experience">
      <ol className="waiting-steps">
        {labels.map((label, index) => (
          <li key={label} className={`waiting-step ${stepState(index, currentStep)}`}>
            <span className="waiting-step-mark" aria-hidden="true" />
            {label}
          </li>
        ))}
      </ol>
      <p className="waiting-tip" key={tipIndex} aria-hidden="true">{tips[tipIndex]}</p>
    </div>
  );
}

function App() {
  const [language, setLanguage] = useState(readLanguage);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState([]);
  const [deploymentVersion, setDeploymentVersion] = useState(null);
  const [savedChats, setSavedChats] = useState(readChatHistory);
  const [activeChatId, setActiveChatId] = useState(null);
  const [historySearch, setHistorySearch] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(
    () => !window.matchMedia("(max-width: 820px)").matches,
  );
  const [apiStatus, setApiStatus] = useState("checking");
  const [activeTopic, setActiveTopic] = useState(null);
  const [composerFocused, setComposerFocused] = useState(false);
  const [exampleIndex, setExampleIndex] = useState(0);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const ui = interfaceText[language];
  const isArabic = language === "ar";
  const getChatTitle = (chat) => (
    chat.displayTitles?.[language]
    || (isArabic ? chat.arabicTitle : chat.title)
    || chat.title
  );

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
    let active = true;
    fetch(`${API_BASE_URL}/version`)
      .then((response) => {
        if (!response.ok) throw new Error("Version unavailable");
        return response.json();
      })
      .then((version) => active && setDeploymentVersion(version))
      .catch(() => active && setDeploymentVersion(null));
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

  const filteredChats = savedChats.filter((chat) =>
    getChatTitle(chat).toLowerCase().includes(historySearch.toLowerCase()),
  );

  const changeLanguage = async (nextLanguage) => {
    setLanguage(nextLanguage);

    const messagesById = new Map();
    for (const message of [
      ...messages,
      ...savedChats.flatMap((chat) => chat.messages),
    ]) {
      if (message.role === "user" || message.role === "assistant") {
        const sourceLanguage = message.language
          || (/[\u0600-\u06ff]/.test(message.text) ? "ar" : "en");
        const existingTranslation = message.displayTranslations?.[nextLanguage]
          || (nextLanguage === "ar" && message.arabicText);
        if (sourceLanguage === nextLanguage || existingTranslation) continue;
        messagesById.set(message.id, message);
      }
    }
    const untranslated = [...messagesById.values()];
    if (!untranslated.length) return;

    try {
      const translations = await translateTextsInBatches(
        untranslated.map((message) => message.text),
        nextLanguage,
      );
      const translationsById = new Map(
        untranslated.map((message, index) => [message.id, translations[index]]),
      );

      setMessages((current) => current.map((message) => (
        translationsById.has(message.id)
          ? {
              ...message,
              displayTranslations: {
                ...message.displayTranslations,
                [nextLanguage]: translationsById.get(message.id),
              },
              ...(nextLanguage === "ar" ? { arabicText: translationsById.get(message.id) } : {}),
            }
          : message
      )));
      setSavedChats((current) => current.map((chat) => {
        const translatedMessages = chat.messages.map((message) => (
          translationsById.has(message.id)
            ? {
                ...message,
                displayTranslations: {
                  ...message.displayTranslations,
                  [nextLanguage]: translationsById.get(message.id),
                },
                ...(nextLanguage === "ar" ? { arabicText: translationsById.get(message.id) } : {}),
              }
            : message
        ));
        const firstUserMessage = translatedMessages.find((message) => message.role === "user");
        const translatedTitle = firstUserMessage && (
          translationsById.get(firstUserMessage.id)
          || firstUserMessage.displayTranslations?.[nextLanguage]
          || firstUserMessage.text
        );
        return {
          ...chat,
          displayTitles: {
            ...chat.displayTitles,
            ...(translatedTitle ? { [nextLanguage]: translatedTitle } : {}),
          },
          arabicTitle: nextLanguage === "ar" ? translatedTitle || chat.arabicTitle : chat.arabicTitle,
          messages: translatedMessages,
        };
      }));
    } catch {
      // Keep original questions visible if translation is temporarily unavailable.
    }
  };

  const startNewConversation = () => {
    if (loading) return;
    setMessages([]);
    setActiveChatId(null);
    setActiveTopic(null);
    setInput("");
    if (window.matchMedia("(max-width: 820px)").matches) {
      setSidebarOpen(false);
    }
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  const toggleSidebar = () => {
    setSidebarOpen((open) => !open);
  };

  const selectNavigationItem = () => startNewConversation();

  const openChat = (chat) => {
    if (loading) return;
    setActiveChatId(chat.id);
    setActiveTopic(null);
    setMessages(chat.messages);
    if (window.matchMedia("(max-width: 820px)").matches) {
      setSidebarOpen(false);
    }
  };

  const sendQuestion = async (rawQuestion = input) => {
    const question = rawQuestion.trim();
    if (!question || loading) return;
    const responseIsArabic = language === "ar" || /[\u0600-\u06ff]/.test(question);

    const chatId = activeChatId || crypto.randomUUID();
    const existingChat = savedChats.find((chat) => chat.id === chatId);
    const title = existingChat?.title || question.slice(0, 52);
    const userMessage = {
      id: crypto.randomUUID(),
      role: "user",
      text: question,
      language: /[\u0600-\u06ff]/.test(question) ? "ar" : "en",
      ...(language === "ar" && /[\u0600-\u06ff]/.test(question)
        ? { arabicText: question }
        : {}),
    };
    const arabicQuestionTranslation = language === "ar"
      ? translateTextsInBatches([question], "ar")
        .then(([translation]) => {
          if (!translation) return null;
          const displayTranslations = { ar: translation };
          setMessages((current) => current.map((message) => (
            message.id === userMessage.id
              ? { ...message, arabicText: translation, displayTranslations }
              : message
          )));
          setSavedChats((current) => current.map((chat) => {
            if (chat.id !== chatId) return chat;
            const translatedMessages = chat.messages.map((message) => (
              message.id === userMessage.id
                ? { ...message, arabicText: translation, displayTranslations }
                : message
            ));
            const firstUserMessage = translatedMessages.find((message) => message.role === "user");
            return {
              ...chat,
              arabicTitle: firstUserMessage?.arabicText || chat.arabicTitle,
              displayTitles: { ...chat.displayTitles, ar: firstUserMessage?.arabicText || chat.title },
              messages: translatedMessages,
            };
          }));
          return translation;
        })
        .catch(() => null)
      : Promise.resolve(null);
    const userMessages = [...messages, userMessage];

    setActiveChatId(chatId);
    setMessages(userMessages);
    setSavedChats((history) => saveChat(history, {
      id: chatId,
      title,
      arabicTitle: existingChat?.arabicTitle,
      displayTitles: existingChat?.displayTitles,
      messages: userMessages,
      updatedAt: Date.now(),
    }));
    setInput("");
    setActiveTopic(null);
    if (window.matchMedia("(max-width: 820px)").matches) {
      setSidebarOpen(false);
    }
    if (inputRef.current) inputRef.current.style.height = "auto";
    setLoading(true);

    const assistantId = crypto.randomUUID();
    const assistantDraft = {
      id: assistantId,
      role: "assistant",
      text: "",
      language: responseIsArabic ? "ar" : "en",
      streaming: true,
      status: "understanding",
    };
    setMessages([...userMessages, assistantDraft]);

    try {
      let streamedText = "";
      let exportResult = null;
      let exportContext = null;
      let conversationContext = null;
      let sources = [];
      let viewResults = [];
      const updateAssistant = (updates) => {
        setMessages((current) => current.map((message) =>
          message.id === assistantId ? { ...message, ...updates } : message,
        ));
      };

      await streamQuestion(question, language, (event, payload) => {
        if (event === "status") updateAssistant({ status: payload.step });
        if (event === "export") {
          exportResult = payload;
          updateAssistant({ export: payload });
        }
        if (event === "context") {
          exportContext = payload.export_context || exportContext;
          conversationContext = payload.conversation_context || conversationContext;
        }
        if (event === "source") {
          sources = mergeSources(sources, payload);
          updateAssistant({ sources });
        }
        if (event === "view_result") {
          viewResults = [...viewResults, payload];
          updateAssistant({ viewResults });
        }
        if (event === "token") {
          streamedText += payload.text;
          updateAssistant({ text: streamedText });
        }
      },
      getLatestExportContext(messages),
      getConversationHistory(messages, 12, language),
      getLatestConversationContext(messages),
      );

      const translatedQuestion = await arabicQuestionTranslation;
      const finalUserMessages = userMessages.map((message) => (
        message.id === userMessage.id && translatedQuestion
          ? {
              ...message,
              arabicText: translatedQuestion,
              displayTranslations: { ...message.displayTranslations, ar: translatedQuestion },
            }
          : message
      ));
      const assistantMessage = {
        ...assistantDraft,
        text: streamedText,
        streaming: false,
        status: null,
        ...(exportResult ? { export: exportResult } : {}),
        ...(exportContext ? { exportContext } : {}),
        ...(conversationContext ? { conversationContext } : {}),
        ...(sources.length === 1 ? { source: sources[0] } : {}),
        ...(sources.length > 1 ? { sources } : {}),
        ...(viewResults.length ? { viewResults } : {}),
      };
      const updatedMessages = [...finalUserMessages, assistantMessage];
      setMessages(updatedMessages);
      setSavedChats((history) => saveChat(history, {
        id: chatId,
        title,
        arabicTitle: finalUserMessages.find((message) => message.role === "user")?.arabicText || existingChat?.arabicTitle,
        displayTitles: {
          ...existingChat?.displayTitles,
          ...(finalUserMessages.find((message) => message.role === "user")?.displayTranslations || {}),
          ...(finalUserMessages.find((message) => message.role === "user")?.arabicText
            ? { ar: finalUserMessages.find((message) => message.role === "user").arabicText }
            : {}),
        },
        messages: updatedMessages,
        updatedAt: Date.now(),
      }));
    } catch (error) {
      const translatedQuestion = await arabicQuestionTranslation;
      const finalUserMessages = userMessages.map((message) => (
        message.id === userMessage.id && translatedQuestion
          ? {
              ...message,
              arabicText: translatedQuestion,
              displayTranslations: { ...message.displayTranslations, ar: translatedQuestion },
            }
          : message
      ));
      const assistantMessage = {
        id: assistantId,
        role: "assistant",
        text: responseIsArabic
          ? interfaceText.ar.genericError
          : error.message || interfaceText.en.genericError,
        language: responseIsArabic ? "ar" : "en",
        error: true,
      };
      const updatedMessages = [...finalUserMessages, assistantMessage];
      setMessages(updatedMessages);
      setSavedChats((history) => saveChat(history, {
        id: chatId,
        title,
        arabicTitle: finalUserMessages.find((message) => message.role === "user")?.arabicText || existingChat?.arabicTitle,
        displayTitles: {
          ...existingChat?.displayTitles,
          ...(finalUserMessages.find((message) => message.role === "user")?.displayTranslations || {}),
          ...(finalUserMessages.find((message) => message.role === "user")?.arabicText
            ? { ar: finalUserMessages.find((message) => message.role === "user").arabicText }
            : {}),
        },
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
    if (window.matchMedia("(max-width: 820px)").matches) {
      setSidebarOpen(false);
    }
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
    <div className={`assistant-app ${sidebarOpen ? "" : "sidebar-hidden"}`} dir={isArabic ? "rtl" : "ltr"}>
      {sidebarOpen && window.matchMedia("(max-width: 820px)").matches && (
        <div className="sidebar-scrim" aria-hidden="true" />
      )}

      <aside className={`sidebar ${sidebarOpen ? "is-open" : ""}`}>
        <div className="sidebar-brand">
          <div className="brand-mark"><Sparkles size={18} /></div>
          <div className="brand-copy">
            <strong>{ui.brand}</strong>
            <span>{ui.brandSubtitle}</span>
          </div>
        </div>

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
                title={getChatTitle(chat)}
              >
                <MessageSquareText size={14} />
                <span>{getChatTitle(chat)}</span>
              </button>
            ))}
          </div>
        </section>

        <div className="sidebar-status">
          <span className={`status-dot ${apiStatus}`} />
          <div>
            <strong>{apiStatus === "connected" ? ui.apiConnected : apiStatus === "offline" ? ui.apiUnavailable : ui.apiChecking}</strong>
            <span>{ui.apiDescription}</span>
            <span
              className="sidebar-version"
              title={deploymentVersion ? `${deploymentVersion.service} · ${deploymentVersion.revision}` : undefined}
            >
              {deploymentVersion
                ? `${ui.version} ${deploymentVersion.version} · ${deploymentVersion.revision === "local" ? (isArabic ? "محلي" : "local") : deploymentVersion.revision.split("-").slice(-2).join("-")}`
                : ui.versionUnavailable}
            </span>
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
              aria-label={sidebarOpen ? ui.closeMenu : ui.openMenu}
              aria-expanded={sidebarOpen}
            >
              <Menu size={19} />
            </button>
            <div>
              <span>{ui.context}</span>
              <strong>
                {activeChatId
                  ? getChatTitle(savedChats.find((chat) => chat.id === activeChatId) || { title: "" }) || ui.askData
                  : ui.askData}
              </strong>
            </div>
          </div>
          <div className="header-actions">
            <div className="language-switch" role="group" aria-label={ui.language}>
              <button
                className={language === "en" ? "selected" : ""}
                onClick={() => changeLanguage("en")}
                aria-pressed={language === "en"}
              >EN</button>
              <button
                className={language === "ar" ? "selected" : ""}
                onClick={() => changeLanguage("ar")}
                aria-pressed={language === "ar"}
              >عربي</button>
            </div>
            {messages.length > 0 && (
              <button
                className="header-new-conversation"
                onClick={startNewConversation}
                disabled={loading}
                aria-label={ui.newConversation}
              >
                <Plus size={15} />
                <span>{ui.newConversation}</span>
              </button>
            )}
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
                  {(() => {
                    const responseText = message.displayTranslations?.[language] || message.text;
                    const viewResults = message.role === "assistant" && message.viewResults?.length
                      ? parseViewResults(message.viewResults)
                      : null;
                    const resultSections = message.role === "assistant" && !viewResults && message.exportContext
                      ? splitResultsMarkdown(responseText)
                      : null;
                    const resultCards = resultSections
                      ? parseResultCards(resultSections.results, resultSections.summary)
                      : null;

                    return <div className={`message-content ${resultSections ? "has-results" : ""}`}>
                    <span className="message-author">{message.role === "user" ? ui.you : ui.assistant}</span>
                    <div className={`message-bubble ${message.role} ${message.error ? "has-error" : ""}`} dir="auto">
                      {message.role === "assistant" && message.streaming && !message.text ? (
                        <div className="thinking-indicator" role="status" aria-live="polite">
                          <span /><span /><span />
                          <p>{ui.processingSteps[processingStatusIndex[message.status] ?? 0]}</p>
                          <WaitingExperience
                            labels={ui.stepLabels}
                            currentStep={processingStatusIndex[message.status] ?? 0}
                            tips={waitingTips[language] || waitingTips.en}
                          />
                        </div>
                      ) : message.role === "assistant" ? (
                        viewResults ? (
                          <>
                            {viewResults.map((viewResult) => {
                              return (
                                <section className="view-result" key={viewResult.view_id}>
                                  <h3>{viewResult.display_name}</h3>
                                  {viewResult.summary && (
                                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{viewResult.summary}</ReactMarkdown>
                                  )}
                                  <div className="results-content" dir="auto">
                                    {viewResult.cards ? (
                                      <div className="result-card-grid" role="list">
                                        {viewResult.cards.map((card, index) => (
                                          <article className="result-card" key={`${card.label}-${index}`} role="listitem">
                                            <span className="result-card-icon" aria-hidden="true">{card.icon}</span>
                                            <div className="result-card-copy">
                                              <span className="result-card-label">{card.label}</span>
                                              {card.value && (
                                                <span className="result-card-value">
                                                  <strong>{card.value}</strong>
                                                  {card.valueLabel && <span>{card.valueLabel}</span>}
                                                </span>
                                              )}
                                              {card.details.length > 0 && (
                                                <span className="result-card-details">
                                                  {card.details.map((detail) => <span key={detail}>{detail}</span>)}
                                                </span>
                                              )}
                                            </div>
                                          </article>
                                        ))}
                                      </div>
                                    ) : (
                                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{viewResult.results}</ReactMarkdown>
                                    )}
                                  </div>
                                  {viewResult.source && (
                                    <details className="source-details">
                                      <summary>{viewResult.source.display_name}</summary>
                                      <dl>
                                        <dt>Dataset</dt><dd>{viewResult.source.dataset}</dd>
                                        <dt>{viewResult.source.object_type || "View"}</dt>
                                        <dd>{viewResult.source.object_name || viewResult.source.view}</dd>
                                      </dl>
                                      <pre dir="ltr"><code>{viewResult.source.sql}</code></pre>
                                    </details>
                                  )}
                                </section>
                              );
                            })}
                          </>
                        ) : resultSections ? (
                          <>
                            {resultSections.summary && (
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                {resultSections.summary}
                              </ReactMarkdown>
                            )}
                            <div className="results-content" dir="auto">
                              {resultCards ? (
                                <div className="result-card-grid" role="list">
                                  {resultCards.map((card, index) => (
                                    <article className="result-card" key={`${card.label}-${index}`} role="listitem">
                                      <span className="result-card-icon" aria-hidden="true">{card.icon}</span>
                                      <div className="result-card-copy">
                                        <span className="result-card-label">{card.label}</span>
                                        {card.value && (
                                          <span className="result-card-value">
                                            <strong>{card.value}</strong>
                                            {card.valueLabel && <span>{card.valueLabel}</span>}
                                          </span>
                                        )}
                                        {card.details.length > 0 && (
                                          <span className="result-card-details">
                                            {card.details.map((detail) => <span key={detail}>{detail}</span>)}
                                          </span>
                                        )}
                                      </div>
                                    </article>
                                  ))}
                                </div>
                              ) : (
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                  {resultSections.results}
                                </ReactMarkdown>
                              )}
                            </div>
                          </>
                        ) : (
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{responseText}</ReactMarkdown>
                        )
                      ) : (
                        message.displayTranslations?.[language]
                        || (language === "ar" ? message.arabicText : null)
                        || message.text
                      )}
                      {message.role === "assistant" && message.streaming && message.text && (
                        <span className="stream-cursor" aria-hidden="true" />
                      )}
                      {message.role === "assistant" && message.export?.download_url && (
                        <a
                          className="export-download"
                          href={resolveDownloadUrl(API_BASE_URL, message.export.download_url)}
                          download
                        >
                          <span aria-hidden="true">📊</span>
                          <strong>{ui.downloadExcel}</strong>
                        </a>
                      )}
                      {message.role === "assistant" && !message.viewResults?.length && (message.sources?.length || message.source) && (
                        (message.sources || [message.source]).map((source) => (
                          <details className="source-details" key={`${source.dataset}.${source.object_name || source.view}`}>
                            <summary>{source.display_name}</summary>
                            <dl>
                              <dt>Dataset</dt><dd>{source.dataset}</dd>
                              <dt>{source.object_type || "View"}</dt>
                              <dd>{source.object_name || source.view}</dd>
                            </dl>
                            <pre dir="ltr"><code>{source.sql}</code></pre>
                          </details>
                        ))
                      )}
                    </div>
                    </div>;
                  })()}
                </div>
              ))}
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