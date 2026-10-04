import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import "./App.css";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

function App() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);

  const [messages, setMessages] = useState([
    {
      role: "assistant",
      text: "Hello! I am the National Sports Platform AI Assistant. How can I help you?",
    },
  ]);

  const messagesEndRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  }, [messages, loading]);

  const isArabic = (text) => {
    if (!text) return false;

    const arabicChars = (text.match(/[\u0600-\u06FF]/g) || []).length;
    const englishChars = (text.match(/[A-Za-z]/g) || []).length;

    return arabicChars > englishChars;
  };

  const handleSend = async () => {
    const text = question.trim();

    if (!text || loading) return;

    setLoading(true);

    setMessages((prev) => [
      ...prev,
      {
        role: "user",
        text,
      },
    ]);

    setQuestion("");

    try {
      console.log("Calling backend:", `${API_BASE_URL}/ask`);

      const response = await fetch(`${API_BASE_URL}/ask`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          question: text,
        }),
      });

      if (!response.ok) {
        const errorText = await response.text();

        console.error(
          "Backend error:",
          response.status,
          errorText
        );

        throw new Error(
          `Backend request failed: ${response.status}`
        );
      }

      const data = await response.json();

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: data.answer,
        },
      ]);
    } catch (error) {
      console.error("Request error:", error);

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: "Sorry, I was unable to process your request.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleClearChat = () => {
    setMessages([
      {
        role: "assistant",
        text: "Hello! I am the National Sports Platform AI Assistant. How can I help you?",
      },
    ]);

    setQuestion("");
  };

  const handleKeyDown = (event) => {
    if (event.key === "Enter") {
      handleSend();
    }
  };

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>National Sports Platform</h1>
          <p>AI Assistant</p>
        </div>

        <div className="header-actions">
          <button
            className="clear-button"
            onClick={handleClearChat}
            disabled={loading}
          >
            Clear Chat
          </button>

          <span className="status">
            <span className="status-dot"></span>
            Online
          </span>
        </div>
      </header>

      <main className="chat-container">
        <div className="messages">
          {messages.map((message, index) => {
            const arabic = isArabic(message.text);

            return (
              <div
                key={index}
                className={`message-row ${message.role}`}
              >
                <div
                  className={`message ${message.role} ${
                    arabic ? "rtl" : ""
                  }`}
                  dir={arabic ? "rtl" : "ltr"}
                >
                  {message.role === "assistant" ? (
                    <ReactMarkdown>
                      {message.text}
                    </ReactMarkdown>
                  ) : (
                    message.text
                  )}
                </div>
              </div>
            );
          })}

          {loading && (
            <div className="message-row assistant">
              <div className="message assistant">
                Analyzing your question...
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        <div className="input-area">
          <input
            type="text"
            value={question}
            onChange={(event) =>
              setQuestion(event.target.value)
            }
            onKeyDown={handleKeyDown}
            placeholder="Ask a question about National Sports Platform data..."
            disabled={loading}
            dir={isArabic(question) ? "rtl" : "ltr"}
          />

          <button
            onClick={handleSend}
            disabled={loading}
          >
            {loading ? "Thinking..." : "Send"}
          </button>
        </div>
      </main>
    </div>
  );
}

export default App;