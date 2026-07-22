(() => {
  "use strict";

  const SPIKE_STORAGE_KEY = "deepagents-spike-active-run";

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
    spikeRuntime: document.querySelector("#spikeRuntime"),
    spikeRuntimeTitle: document.querySelector("#spikeRuntimeTitle"),
    spikeRunBadge: document.querySelector("#spikeRunBadge"),
    spikeProgressLabel: document.querySelector("#spikeProgressLabel"),
    spikeEventCount: document.querySelector("#spikeEventCount"),
    spikePlanList: document.querySelector("#spikePlanList"),
    spikeTimeline: document.querySelector("#spikeTimeline"),
    cancelSpikeButton: document.querySelector("#cancelSpikeButton"),
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
      multi_parcel_resolution: "一单多包裹调查",
      crossborder_customs: "跨境海关调查",
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
      agent_planning: "Agent 规划与执行",
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
    spikeStatuses: {
      queued: "排队中",
      running: "执行中",
      waiting_input: "等待补充信息",
      waiting_approval: "等待确认",
      completed: "已完成",
      failed: "失败",
      cancelled: "已取消",
    },
  };

  const state = {
    sessionId: createSessionId(),
    userId: "web-demo-user",
    language: "zh-CN",
    busy: false,
    composing: false,
    mode: "chat",
    spikeRunId: null,
    spikeAccessToken: null,
    spikeStatus: null,
    spikeCheckpointVersion: 0,
    spikePollTimer: null,
    spikeCheckpointAnnounced: 0,
    spikeTerminalAnnounced: null,
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

  function persistSpikeRun() {
    if (!state.spikeRunId || !state.spikeAccessToken) return;
    try {
      sessionStorage.setItem(
        SPIKE_STORAGE_KEY,
        JSON.stringify({
          runId: state.spikeRunId,
          accessToken: state.spikeAccessToken,
          sessionId: state.sessionId,
        }),
      );
    } catch {
      // Session restoration is a Demo convenience; the active Run itself is unaffected.
    }
  }

  function clearStoredSpikeRun() {
    try {
      sessionStorage.removeItem(SPIKE_STORAGE_KEY);
    } catch {
      // Ignore browsers that disable session storage.
    }
  }

  function restoreStoredSpikeRun() {
    try {
      const raw = sessionStorage.getItem(SPIKE_STORAGE_KEY);
      if (!raw) return false;
      const stored = JSON.parse(raw);
      if (!stored.runId || !stored.accessToken || !stored.sessionId) {
        clearStoredSpikeRun();
        return false;
      }
      state.mode = "spike";
      state.spikeRunId = stored.runId;
      state.spikeAccessToken = stored.accessToken;
      state.spikeStatus = "queued";
      state.sessionId = stored.sessionId;
      elements.spikeRuntime.hidden = false;
      elements.spikeRuntimeTitle.textContent = "正在恢复 DeepAgents 长任务";
      const divider = createElement("div", "date-divider");
      divider.append(createElement("span", "", "恢复 DeepAgents Spike"));
      elements.messages.append(divider);
      addMessage("assistant", "已从当前浏览器标签恢复长任务标识，正在重新加载 Plan 和执行时间线。");
      updateSessionLabel();
      pollSpikeRun();
      return true;
    } catch {
      clearStoredSpikeRun();
      return false;
    }
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
        button.addEventListener("click", () => {
          if (reply.spikeDecision) {
            resumeSpikeRun(reply.spikeDecision, reply.value || "");
          } else {
            sendMessage(reply.value);
          }
        });
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

  function stopSpikePolling() {
    if (state.spikePollTimer) {
      clearTimeout(state.spikePollTimer);
      state.spikePollTimer = null;
    }
  }

  function setSpikeInteraction(status) {
    const running = status === "queued" || status === "running";
    const waitingInput = status === "waiting_input";
    const waitingApproval = status === "waiting_approval";
    setBusy(running);
    if (waitingInput || waitingApproval) {
      setBusy(false);
      elements.messageInput.disabled = waitingApproval;
      elements.sendButton.disabled = waitingApproval;
      document.querySelectorAll(".scenario-button").forEach((button) => {
        button.disabled = true;
      });
    }
    if (!running && !waitingInput && !waitingApproval) {
      setBusy(false);
    }
    elements.cancelSpikeButton.disabled = !running && !waitingInput && !waitingApproval;
  }

  function spikeQuickReplies(snapshot) {
    const actions = snapshot.pending_action?.actions || [];
    const names = actions.map((item) => item.name);
    if (snapshot.status === "waiting_approval") {
      return [
        { label: "批准并继续", value: "确认执行", spikeDecision: "approve" },
        { label: "拒绝并重规划", value: "拒绝本次操作", spikeDecision: "reject" },
      ];
    }
    if (snapshot.status !== "waiting_input") return [];
    if (names.includes("request_compliance_decision")) {
      return [
        { label: "模拟合规批准", value: "合规批准", spikeDecision: "respond" },
        { label: "模拟合规拒绝", value: "合规拒绝", spikeDecision: "respond" },
      ];
    }
    if (snapshot.scenario === "crossborder_customs") {
      return [
        {
          label: "上传有效材料包",
          value: "我补充材料 DOC-BAT-VALID 和 DOC-LIQ-VALID，请连同已有 DOC-INVOICE-001 一起检查",
          spikeDecision: "respond",
        },
        {
          label: "上传过期电池报告",
          value: "我只有 DOC-BAT-EXPIRED 和 DOC-LIQ-VALID，请连同已有 DOC-INVOICE-001 一起检查；没有其他有效电池报告，如果不通过就按原条件退回",
          spikeDecision: "respond",
        },
      ];
    }
    return [
      {
        label: "补充测试地址和手机号",
        value: "新地址是 123 Nguyen Hue Street, District 1，手机号后四位 1234",
        spikeDecision: "respond",
      },
    ];
  }

  function renderSpikePlan(plan = []) {
    elements.spikePlanList.innerHTML = "";
    if (!plan.length) {
      const item = createElement("li", "", "DeepAgent 正在生成动态计划…");
      item.dataset.status = "in_progress";
      elements.spikePlanList.append(item);
      elements.spikeProgressLabel.textContent = "规划中";
      return;
    }
    let completed = 0;
    plan.forEach((step) => {
      const item = createElement("li", "", step.content);
      item.dataset.status = step.status;
      if (step.status === "completed") completed += 1;
      elements.spikePlanList.append(item);
    });
    elements.spikeProgressLabel.textContent = `已完成 ${completed}/${plan.length} 步`;
  }

  function renderSpikeTimeline(events = []) {
    elements.spikeTimeline.innerHTML = "";
    events.slice(-40).forEach((event) => {
      const row = createElement("div", "timeline-event");
      row.dataset.source = event.source;
      row.dataset.status = event.status;
      const dot = document.createElement("i");
      dot.setAttribute("aria-hidden", "true");
      const title = createElement("span", "", event.title);
      const time = createElement(
        "time",
        "",
        new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(
          new Date(event.occurred_at),
        ),
      );
      row.title = JSON.stringify(event.safe_data || {}, null, 2);
      row.append(dot, title, time);
      elements.spikeTimeline.append(row);
    });
    elements.spikeEventCount.textContent = `${events.length} 个事件`;
    elements.spikeTimeline.scrollTop = elements.spikeTimeline.scrollHeight;
  }

  function renderSpikeSnapshot(snapshot) {
    state.spikeStatus = snapshot.status;
    state.spikeCheckpointVersion = snapshot.checkpoint_version;
    elements.spikeRuntime.hidden = false;
    elements.spikeRuntimeTitle.textContent = snapshot.scenario_title;
    elements.spikeRunBadge.textContent = labels.spikeStatuses[snapshot.status] || snapshot.status;
    elements.spikeRunBadge.dataset.status = snapshot.status;
    renderSpikePlan(snapshot.plan);
    renderSpikeTimeline(snapshot.events);
    setSpikeInteraction(snapshot.status);

    elements.intentValue.textContent = labels.intents[snapshot.scenario] || snapshot.scenario_title;
    elements.stepValue.textContent = "Agent 规划与执行";
    elements.statusValue.textContent = labels.spikeStatuses[snapshot.status] || snapshot.status;
    elements.statusValue.dataset.status =
      snapshot.status === "waiting_input" || snapshot.status === "waiting_approval"
        ? "waiting_confirmation"
        : snapshot.status;

    const details = {
      run_id: snapshot.run_id,
      plan: snapshot.plan,
      events: snapshot.events,
      pending_action: snapshot.pending_action,
      result: snapshot.result,
      trace_id: snapshot.trace_id,
    };
    if (
      (snapshot.status === "waiting_input" || snapshot.status === "waiting_approval") &&
      snapshot.checkpoint_version > state.spikeCheckpointAnnounced
    ) {
      state.spikeCheckpointAnnounced = snapshot.checkpoint_version;
      addMessage("assistant", snapshot.reply || snapshot.pending_action?.prompt || "任务已暂停。", {
        data: details,
        quickReplies: spikeQuickReplies(snapshot),
      });
    }
    if (
      ["completed", "failed", "cancelled"].includes(snapshot.status) &&
      state.spikeTerminalAnnounced !== snapshot.status
    ) {
      state.spikeTerminalAnnounced = snapshot.status;
      addMessage("assistant", snapshot.reply || "长任务已结束。", { data: details });
      state.mode = "chat";
      stopSpikePolling();
      clearStoredSpikeRun();
    }
  }

  async function fetchSpikeSnapshot() {
    if (!state.spikeRunId || !state.spikeAccessToken) return null;
    const response = await fetch(`/v1/deep-agent/runs/${encodeURIComponent(state.spikeRunId)}`, {
      headers: { "X-Spike-Access-Token": state.spikeAccessToken },
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    return payload;
  }

  async function pollSpikeRun() {
    stopSpikePolling();
    try {
      const snapshot = await fetchSpikeSnapshot();
      if (!snapshot) return;
      renderSpikeSnapshot(snapshot);
      if (["queued", "running"].includes(snapshot.status)) {
        state.spikePollTimer = setTimeout(pollSpikeRun, 850);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "未知错误";
      showError(`无法读取长任务状态：${message}`);
      state.spikePollTimer = setTimeout(pollSpikeRun, 1800);
    }
  }

  async function startSpikeRun(scenario, prompt) {
    if (state.busy) return;
    stopSpikePolling();
    clearStoredSpikeRun();
    hideError();
    state.mode = "spike";
    state.spikeRunId = null;
    state.spikeAccessToken = null;
    state.spikeStatus = "queued";
    state.spikeCheckpointVersion = 0;
    state.spikeCheckpointAnnounced = 0;
    state.spikeTerminalAnnounced = null;
    state.sessionId = createSessionId();
    updateSessionLabel();
    elements.messages.innerHTML = "";
    const divider = createElement("div", "date-divider");
    divider.append(createElement("span", "", "DeepAgents Spike"));
    elements.messages.append(divider);
    addMessage("user", prompt);
    elements.spikeRuntime.hidden = false;
    elements.spikeRuntimeTitle.textContent = labels.intents[scenario] || "DeepAgents 长任务";
    renderSpikePlan([]);
    renderSpikeTimeline([]);
    setBusy(true);
    const typing = addTypingIndicator();

    try {
      const response = await fetch("/v1/deep-agent/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: state.sessionId,
          user_id: state.userId,
          scenario,
          message: prompt,
          language: state.language,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      state.spikeRunId = payload.run_id;
      state.spikeAccessToken = payload.access_token;
      persistSpikeRun();
      typing.remove();
      addMessage(
        "assistant",
        "长任务已经进入独立 DeepAgents Runtime。执行计划、子 Agent 调查和 Tool 证据会显示在上方；需要资料或确认时任务会安全暂停。",
      );
      pollSpikeRun();
    } catch (error) {
      typing.remove();
      state.mode = "chat";
      setBusy(false);
      const message = error instanceof Error ? error.message : "未知错误";
      showError(`无法创建 DeepAgents 长任务：${message}`);
      addMessage("assistant", "长任务没有创建成功，请检查模型和本地服务配置后，再从左侧选择案例。");
    }
  }

  async function resumeSpikeRun(decision, message = "") {
    if (!state.spikeRunId || !state.spikeAccessToken || state.busy) return;
    const display =
      decision === "approve" ? "确认执行这些操作" : decision === "reject" ? "拒绝，请重新规划" : message;
    addMessage("user", display);
    hideError();
    setBusy(true);
    const typing = addTypingIndicator();
    try {
      const response = await fetch(
        `/v1/deep-agent/runs/${encodeURIComponent(state.spikeRunId)}/resume`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            access_token: state.spikeAccessToken,
            checkpoint_version: state.spikeCheckpointVersion,
            decision,
            message: decision === "approve" ? null : message || null,
          }),
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      typing.remove();
      renderSpikeSnapshot(payload);
      pollSpikeRun();
    } catch (error) {
      typing.remove();
      setBusy(false);
      const errorMessage = error instanceof Error ? error.message : "未知错误";
      showError(`无法恢复长任务：${errorMessage}`);
    }
  }

  async function cancelSpikeRun() {
    if (!state.spikeRunId || !state.spikeAccessToken) return;
    stopSpikePolling();
    try {
      const response = await fetch(`/v1/deep-agent/runs/${encodeURIComponent(state.spikeRunId)}`, {
        method: "DELETE",
        headers: { "X-Spike-Access-Token": state.spikeAccessToken },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      renderSpikeSnapshot(payload);
    } catch (error) {
      const message = error instanceof Error ? error.message : "未知错误";
      showError(`无法取消长任务：${message}`);
    }
  }

  async function sendMessage(message) {
    const value = (message ?? elements.messageInput.value).trim();
    if (!value || state.busy) return;

    if (state.mode === "spike") {
      if (state.spikeStatus === "waiting_input") {
        elements.messageInput.value = "";
        resizeTextarea();
        await resumeSpikeRun("respond", value);
      }
      return;
    }

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

  async function resetConversation() {
    if (
      state.spikeRunId &&
      ["queued", "running", "waiting_input", "waiting_approval"].includes(state.spikeStatus)
    ) {
      await cancelSpikeRun();
    }
    stopSpikePolling();
    state.mode = "chat";
    state.spikeRunId = null;
    state.spikeAccessToken = null;
    state.spikeStatus = null;
    state.spikeCheckpointVersion = 0;
    state.spikeCheckpointAnnounced = 0;
    state.spikeTerminalAnnounced = null;
    clearStoredSpikeRun();
    elements.spikeRuntime.hidden = true;
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
  elements.newConversationButton.addEventListener("click", () => resetConversation());
  elements.languageSelect.addEventListener("change", (event) => {
    state.language = event.target.value;
  });
  elements.dismissErrorButton.addEventListener("click", hideError);
  elements.closeDialogButton.addEventListener("click", () => elements.dataDialog.close());
  elements.dataDialog.addEventListener("click", (event) => {
    if (event.target === elements.dataDialog) elements.dataDialog.close();
  });
  elements.cancelSpikeButton.addEventListener("click", cancelSpikeRun);
  document.querySelectorAll(".scenario-button:not(.spike-button)").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.language) {
        state.language = button.dataset.language;
        elements.languageSelect.value = button.dataset.language;
      }
      sendMessage(button.dataset.prompt || "");
    });
  });
  document.querySelectorAll(".spike-button").forEach((button) => {
    button.addEventListener("click", () => {
      startSpikeRun(button.dataset.spikeScenario || "", button.dataset.prompt || "");
    });
  });

  updateSessionLabel();
  checkConnection();
  restoreStoredSpikeRun();
  elements.messageInput.focus();
})();
