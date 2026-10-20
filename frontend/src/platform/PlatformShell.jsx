import { Component, lazy, Suspense, useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from "react";
import { FlaskConical, MessageSquareText, Workflow } from "lucide-react";
import App from "../App.jsx";
import { getUseCase, navigateUseCase, buildUseCaseUrl } from "./navigation.js";
import "./platform.css";

const LicenseDelayPage = lazy(() => import("../uc2/LicenseDelayPage.jsx"));

class DashboardBoundary extends Component {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed
      ? <div className="platform-loading" role="alert">The synthetic dashboard could not be loaded.</div>
      : this.props.children;
  }
}

export default function PlatformShell() {
  const [activeView, setActiveView] = useState(() => getUseCase(window.location.search));
  const [visitedUC2, setVisitedUC2] = useState(() => getUseCase(window.location.search) === "uc2");
  const currentView = useRef(activeView);
  const positions = useRef({ uc1: 0, uc2: 0 });
  const focusTargets = useRef({});
  const uc1Panel = useRef(null);
  const uc2Panel = useRef(null);
  const initialFocus = useRef(true);

  function activate(view) {
    if (view === currentView.current) return;
    positions.current[currentView.current] = window.scrollY;
    currentView.current = view;
    if (view === "uc2") setVisitedUC2(true);
    setActiveView(view);
  }

  const onHistoryNavigation = useEffectEvent(() => {
    const view = getUseCase(window.location.search);
    activate(view);
    if (window.location.hash) {
      requestAnimationFrame(() => {
        const target = document.getElementById(window.location.hash.slice(1));
        if (target?.closest(`[data-usecase="${view}"]`)) target.scrollIntoView();
      });
    }
  });

  useEffect(() => {
    const previous = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";
    const listener = () => onHistoryNavigation();
    window.addEventListener("popstate", listener);
    return () => {
      window.history.scrollRestoration = previous;
      window.removeEventListener("popstate", listener);
    };
  }, []);

  useLayoutEffect(() => {
    if (initialFocus.current && activeView === "uc1") {
      initialFocus.current = false;
      return;
    }
    initialFocus.current = false;
    const panel = activeView === "uc1" ? uc1Panel.current : uc2Panel.current;
    const remembered = focusTargets.current[activeView];
    const target = remembered?.isConnected && panel?.contains(remembered) ? remembered : panel;
    target?.focus({ preventScroll: true });
    window.scrollTo({ top: positions.current[activeView], behavior: "instant" });
  }, [activeView]);

  function navigate(event, view) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (navigateUseCase(window.history, window.location, view)) activate(view);
  }

  return <div className="platform-shell">
    <header className="platform-bar" lang="en" dir="ltr">
      <div className="platform-brand"><FlaskConical size={19} aria-hidden="true" /><div><strong>National Sports Platform AI</strong><span>Local development</span></div></div>
      <nav aria-label="Use cases" className="platform-navigation">
        <a href={buildUseCaseUrl(window.location.href, "uc1")} aria-current={activeView === "uc1" ? "page" : undefined} onClick={(event) => navigate(event, "uc1")}><MessageSquareText size={16} aria-hidden="true" /><span>Conversational AI <small>UC1</small></span></a>
        <a href={buildUseCaseUrl(window.location.href, "uc2")} aria-current={activeView === "uc2" ? "page" : undefined} onClick={(event) => navigate(event, "uc2")}><Workflow size={16} aria-hidden="true" /><span>License Delay Analysis <small>Synthetic Demo · UC2</small></span></a>
      </nav>
    </header>
    <div ref={uc1Panel} className="platform-view" data-usecase="uc1" role="region" aria-label="Conversational AI" tabIndex={-1} hidden={activeView !== "uc1"} inert={activeView !== "uc1"} aria-hidden={activeView !== "uc1"} onFocusCapture={(event) => { focusTargets.current.uc1 = event.target; }}>
      <App />
    </div>
    <div ref={uc2Panel} className="platform-view" data-usecase="uc2" role="region" aria-label="License Delay Analysis Synthetic Demo" lang="en" dir="ltr" tabIndex={-1} hidden={activeView !== "uc2"} inert={activeView !== "uc2"} aria-hidden={activeView !== "uc2"} onFocusCapture={(event) => { focusTargets.current.uc2 = event.target; }}>
      {visitedUC2 && <DashboardBoundary><Suspense fallback={<div className="platform-loading" role="status">Loading synthetic dashboard</div>}><LicenseDelayPage /></Suspense></DashboardBoundary>}
    </div>
  </div>;
}