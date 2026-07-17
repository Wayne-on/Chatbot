(() => {
  "use strict";

  const elements = {
    messages: document.querySelector("#messages"),
    messageInput: document.querySelector("#messageInput"),
    sendButton: document.querySelector("#sendButton"),
    newConversationButton: document.querySelector("#newConversationButton"),
    languageSelect: document.querySelector("#languageSelect"),
    intentValue: document.querySelector("#intentValue"),
    stepValue: document.querySelector("#stepValue"),
    statusValue: document.querySelector("#statusValue"),
    sessionLabel: document.querySelector("#sessionLabel"),
    connectionDot: document.querySelector("#connectionDot"),
    connectionLabel: document.querySelector("#connectionLabel"),
    errorBanner: document.querySelector("#errorBanner"),
    errorText: document.querySelector("#errorText"),
    dismissErrorButton: document.querySelector("#dismissErrorButton"),
    dataDialog: document.querySelector("#dataDialog"),
    dataContent: document.querySelector("#dataContent"),
    closeDialogButton: document.querySelector("#closeDialogButton"),
  };

  const labels = {
    intents: {
      tracking: "物流查询",
      query_package_volume: "包裹体积",
      delivery_followup: "催促派送",
      delivered_not_received: "签收未收到",
      change_address: "修改地址",
      complaint: "投诉 / 理赔",
      query_complaint: "工单查询",
      faq: "政策咨询",
      conversation: "上下文对话",
      fallback: "等待澄清",
    },
    steps: {
      waiting_intent: "等待说明问题",
      waiting_waybill: "等待运单号",
      waiting_phone_last4: "等待手机后四位",
      waiting_new_address: "等待新地址",
      waiting_complaint_description: "等待问题描述",
      waiting_ticket_id: "等待工单号",
      waiting_confirmation: "等待确认",
      retry_tool: "等待重试",
      identity_failed: "身份校验失败",
      human_queue: "人工队列",
      completed: "处理完成",
      action_cancelled: "操作已取消",
    },
    statuses: {
      idle: "空闲",
      collecting: "收集信息",
      processing: "处理中",
      waiting_confirmation: "等待确认",
      completed: "已完成",
      failed: "处理失败",
      transfer: "转人工",
      cancelled: "已取消",
    },
  };

  const state = {
    sessionId: createSessionId(),
    userId: "web-demo-user",
    language: "zh-CN",
    busy: false,
    composing: false,
  };

  function createSessionId() {
    const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    return `web-${random}`;
  }

  function nowLabel() {
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date());
  }

  function shortSessionId(sessionId) {
    return sessionId.length > 22 ? `${sessionId.slice(0, 12)}…${sessionId.slice(-6)}` : sessionId;
  }

  function updateSessionLabel() {
    elements.sessionLabel.textContent = `SESSION  ${shortSessionId(state.sessionId)}`;
    elements.sessionLabel.title = state.sessionId;
  }

  function setStateDisplay(response = {}) {
    const intent = response.current_intent;
    const step = response.current_step;
    const status = response.status || "idle";
    elements.intentValue.textContent = labels.intents[intent] || intent || "等待识别";
    elements.stepValue.textContent = labels.steps[step] || step || "未开始";
    elements.statusValue.textContent = labels.statuses[status] || status;
    elements.statusValue.dataset.status = status;
  }

  function setBusy(busy) {
    state.busy = busy;
    elements.sendButton.disabled = busy;
    elements.messageInput.disabled = busy;
    elements.newConversationButton.disabled = busy;
    document.querySelectorAll(".scenario-button, .quick-reply").forEach((button) => {
      button.disabled = busy;
    });
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      elements.messages.scrollTop = elements.messages.scrollHeight;
    });
  }

  function createElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function addMessage(role, text, options = {}) {
    const article = createElement(
      "article",
      `message ${role === "user" ? "user-message" : "assistant-message"}`,
    );
    const avatar = createElement(
      "div",
      `avatar ${role === "user" ? "user-avatar" : "assistant-avatar"}`,
      role === "user" ? "YOU" : "AI",
    );
    avatar.setAttribute("aria-hidden", "true");

    const column = createElement("div", "message-column");
    const meta = createElement("div", "message-meta");
    meta.append(
      createElement("strong", "", role === "user" ? "你" : "Logistics Copilot"),
      createElement("span", "", nowLabel()),
    );
    const bubble = createElement(
      "div",
      `bubble ${role === "user" ? "user-bubble" : "assistant-bubble"}`,
      text,
    );
    column.append(meta, bubble);

    if (options.data && Object.keys(options.data).length > 0) {
      const actions = createElement("div", "message-actions");
      const planOnly =
        Array.isArray(options.data.planned_intents) &&
        Object.keys(options.data).every((key) => key === "planned_intents");
      const dataButton = createElement(
        "button",
        "data-button",
        planOnly ? "查看处理计划" : "查看 Tool 数据",
      );
      dataButton.type = "button";
      dataButton.addEventListener("click", () => showData(options.data));
      actions.append(dataButton);
      column.append(actions);
    }

    if (options.quickReplies?.length) {
      const replies = createElement("div", "quick-replies");
      options.quickReplies.forEach((reply) => {
        const button = createElement("button", "quick-reply", reply.label);
        button.type = "button";
        button.addEventListener("click", () => sendMessage(reply.value));
        replies.append(button);
      });
      column.append(replies);
    }

    article.append(avatar, column);
    elements.messages.append(article);
    scrollToBottom();
    return article;
  }

  function addTypingIndicator() {
    const article = createElement("article", "message assistant-message typing-message");
    const avatar = createElement("div", "avatar assistant-avatar", "AI");
    avatar.setAttribute("aria-hidden", "true");
    const column = createElement("div", "message-column");
    const meta = createElement("div", "message-meta");
    meta.append(
      createElement("strong", "", "Logistics Copilot"),
      createElement("span", "", "正在处理"),
    );
    const bubble = createElement("div", "bubble assistant-bubble typing-bubble");
    bubble.setAttribute("aria-label", "助手正在输入");
    bubble.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
    column.append(meta, bubble);
    article.append(avatar, column);
    elements.messages.append(article);
    scrollToBottom();
    return article;
  }

  function quickRepliesFor(response) {
    const action = response.action_required;
    if (action === "provide_waybill_no") {
      const samples = {
        delivered_not_received: "JT123456785",
        delivery_followup: "JT123456787",
        change_address: "JT123456781",
        query_package_volume: "JT123456781",
        complaint: "JT123456781",
        tracking: "JT123456781",
      };
      const value = samples[response.current_intent] || "JT123456781";
      return [{ label: `使用测试运单 ${value}`, value }];
    }
    if (action === "provide_phone_last4") {
      return [{ label: "使用测试后四位 1234", value: "1234" }];
    }
    if (action === "provide_new_address") {
      return [
        {
          label: "使用测试地址",
          value: "新地址是 123 Nguyen Hue Street, District 1",
        },
      ];
    }
    if (action === "confirm_action") {
      return [
        { label: "确认执行", value: "确认" },
        { label: "取消操作", value: "取消" },
      ];
    }
    if (action === "clarify_intent") {
      return [
        { label: "查询快递", value: "帮我查一下快递" },
        { label: "转人工", value: "我要人工客服" },
      ];
    }
    return [];
  }

  function showData(data) {
    elements.dataContent.textContent = JSON.stringify(data, null, 2);
    elements.dataDialog.showModal();
  }

  function showError(message) {
    elements.errorText.textContent = message;
    elements.errorBanner.hidden = false;
  }

  function hideError() {
    elements.errorBanner.hidden = true;
  }

  async function sendMessage(message) {
    const value = (message ?? elements.messageInput.value).trim();
    if (!value || state.busy) return;

    hideError();
    addMessage("user", value);
    elements.messageInput.value = "";
    resizeTextarea();
    setBusy(true);
    const typing = addTypingIndicator();

    try {
      const response = await fetch("/v1/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: state.sessionId,
          user_id: state.userId,
          message: value,
          language: state.language,
        }),
      });

      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload.detail ? JSON.stringify(payload.detail) : `HTTP ${response.status}`;
        throw new Error(detail);
      }

      typing.remove();
      setStateDisplay(payload);
      addMessage("assistant", payload.reply, {
        data: payload.data,
        quickReplies: quickRepliesFor(payload),
      });
    } catch (error) {
      typing.remove();
      const messageText = error instanceof Error ? error.message : "未知错误";
      showError(`无法连接客服服务：${messageText}`);
      addMessage("assistant", "这次请求没有完成。请确认服务正在运行，然后重试。", {
        quickReplies: [{ label: "重新发送", value }],
      });
    } finally {
      setBusy(false);
      elements.messageInput.focus();
    }
  }

  function resizeTextarea() {
    elements.messageInput.style.height = "auto";
    elements.messageInput.style.height = `${Math.min(elements.messageInput.scrollHeight, 130)}px`;
  }

  function resetConversation() {
    state.sessionId = createSessionId();
    elements.messages.innerHTML = "";
    const divider = createElement("div", "date-divider");
    divider.append(createElement("span", "", "新对话"));
    elements.messages.append(divider);
    addMessage("assistant", "新会话已建立。请描述你的物流问题，或者选择左侧测试场景。", {
      quickReplies: [
        { label: "查询快递", value: "帮我查一下快递" },
        { label: "签收未收到", value: "我的快递显示签收了，但是我没有收到" },
      ],
    });
    setStateDisplay();
    updateSessionLabel();
    hideError();
    elements.messageInput.focus();
  }

  async function checkConnection() {
    try {
      const response = await fetch("/ready", { cache: "no-store" });
      if (!response.ok) throw new Error("not ready");
      elements.connectionDot.className = "connection-dot connected";
      elements.connectionLabel.textContent = "服务连接正常";
    } catch {
      elements.connectionDot.className = "connection-dot disconnected";
      elements.connectionLabel.textContent = "服务未连接";
    }
  }

  elements.sendButton.addEventListener("click", () => sendMessage());
  elements.messageInput.addEventListener("input", resizeTextarea);
  elements.messageInput.addEventListener("compositionstart", () => {
    state.composing = true;
  });
  elements.messageInput.addEventListener("compositionend", () => {
    state.composing = false;
  });
  elements.messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !state.composing) {
      event.preventDefault();
      sendMessage();
    }
  });
  elements.newConversationButton.addEventListener("click", resetConversation);
  elements.languageSelect.addEventListener("change", (event) => {
    state.language = event.target.value;
  });
  elements.dismissErrorButton.addEventListener("click", hideError);
  elements.closeDialogButton.addEventListener("click", () => elements.dataDialog.close());
  elements.dataDialog.addEventListener("click", (event) => {
    if (event.target === elements.dataDialog) elements.dataDialog.close();
  });
  document.querySelectorAll(".scenario-button").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.language) {
        state.language = button.dataset.language;
        elements.languageSelect.value = button.dataset.language;
      }
      sendMessage(button.dataset.prompt || "");
    });
  });

  updateSessionLabel();
  checkConnection();
  elements.messageInput.focus();
})();
