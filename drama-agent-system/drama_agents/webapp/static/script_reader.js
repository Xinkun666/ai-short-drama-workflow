(function () {
  const root = document.querySelector("[data-script-reader]");
  if (!root) {
    return;
  }

  const generationId = root.dataset.scriptReader;
  const display = root.querySelector("[data-script-display]");
  const editor = root.querySelector("[data-script-editor]");
  const editStart = root.querySelector("[data-edit-start]");
  const editSave = root.querySelector("[data-edit-save]");
  const editCancel = root.querySelector("[data-edit-cancel]");
  const editStatus = root.querySelector("[data-edit-status]");
  const ragChat = root.querySelector("[data-rag-chat]");
  const ragComposer = root.querySelector("[data-rag-composer]");
  const ragAsk = root.querySelector("[data-rag-ask]");
  const ragStatus = root.querySelector("[data-rag-status]");
  const conversationTitle = root.querySelector("[data-conversation-title]");
  const conversationNew = root.querySelector("[data-conversation-new]");
  const conversationToggle = root.querySelector("[data-conversation-toggle]");
  const conversationList = root.querySelector("[data-conversation-list]");
  const selectionCard = root.querySelector("[data-selection-card]");
  const selectionSummary = root.querySelector("[data-selection-summary]");
  const selectionExplain = root.querySelector("[data-selection-explain]");
  const selectionReview = root.querySelector("[data-selection-review]");
  const selectionEdit = root.querySelector("[data-selection-edit]");
  const selectionHistory = root.querySelector("[data-selection-history]");
  const selectionHistoryPanel = root.querySelector("[data-selection-history-panel]");
  const selectionClear = root.querySelector("[data-selection-clear]");
  const initialChatHtml = ragChat.innerHTML;
  const defaultComposerPlaceholder = ragComposer.getAttribute("placeholder") || "";
  let currentConversationId = "";
  let conversations = [];
  let selectedText = "";
  let selectedSelection = null;
  let pendingSelectionIntent = "";
  let isPointerSelecting = false;
  let historyRequestToken = 0;

  function setEditMode(enabled) {
    display.hidden = enabled;
    editor.hidden = !enabled;
    editStart.hidden = enabled;
    editSave.hidden = !enabled;
    editCancel.hidden = !enabled;
    if (enabled) {
      editor.focus();
    }
  }

  function readDisplaySelection() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) {
      return null;
    }
    const range = selection.getRangeAt(0);
    if (!display.contains(range.commonAncestorContainer)) {
      return null;
    }
    const text = selection.toString().trim();
    if (!text) {
      return null;
    }
    const paragraph = paragraphForRange(range);
    return {
      text,
      paragraph_id: paragraph ? paragraph.id : "",
      start_offset: paragraph ? offsetWithin(paragraph, range.startContainer, range.startOffset) : null,
      end_offset: paragraph ? offsetWithin(paragraph, range.endContainer, range.endOffset) : null,
    };
  }

  function paragraphForRange(range) {
    const startElement = range.startContainer.nodeType === Node.TEXT_NODE
      ? range.startContainer.parentElement
      : range.startContainer;
    const endElement = range.endContainer.nodeType === Node.TEXT_NODE
      ? range.endContainer.parentElement
      : range.endContainer;
    const startParagraph = startElement ? startElement.closest("p[id^='script-paragraph-']") : null;
    const endParagraph = endElement ? endElement.closest("p[id^='script-paragraph-']") : null;
    return startParagraph && startParagraph === endParagraph ? startParagraph : null;
  }

  function offsetWithin(paragraph, container, offset) {
    const range = document.createRange();
    range.selectNodeContents(paragraph);
    range.setEnd(container, offset);
    return range.toString().length;
  }

  function updateSelectedText() {
    if (isPointerSelecting) {
      return;
    }
    const nextSelection = readDisplaySelection();
    if (!nextSelection) {
      return;
    }
    selectedSelection = nextSelection;
    selectedText = nextSelection.text;
    pendingSelectionIntent = "";
    ragComposer.placeholder = defaultComposerPlaceholder;
    renderSelectionCard();
  }

  function syncSelectionAfterPointerUp() {
    isPointerSelecting = false;
    window.setTimeout(updateSelectedText, 0);
  }

  function renderSelectionCard() {
    if (!selectionCard || !selectedSelection || !selectedText) {
      if (selectionCard) {
        selectionCard.hidden = true;
      }
      return;
    }
    const paragraphLabel = selectedSelection.paragraph_id
      ? `已选中第 ${selectedSelection.paragraph_id.replace("script-paragraph-", "")} 段`
      : "已选中跨段文本";
    selectionSummary.textContent = `${paragraphLabel} / ${selectedText.length} 字`;
    selectionCard.hidden = false;
  }

  function clearSelection() {
    selectedText = "";
    selectedSelection = null;
    pendingSelectionIntent = "";
    ragComposer.placeholder = defaultComposerPlaceholder;
    if (selectionCard) {
      selectionCard.hidden = true;
    }
    if (selectionHistoryPanel) {
      selectionHistoryPanel.hidden = true;
      selectionHistoryPanel.innerHTML = "";
    }
    const selection = window.getSelection();
    if (selection) {
      selection.removeAllRanges();
    }
    ragStatus.textContent = "";
  }

  function addMessage(role, content, result) {
    const message = document.createElement("div");
    message.className = `script-rag-message ${role}`;
    const paragraph = document.createElement("p");
    paragraph.textContent = content;
    message.appendChild(paragraph);
    const normalizedResult = typeof result === "string" ? { replacement: result } : (result || {});
    const replacement = normalizedResult.replacement || "";
    if (replacement) {
      const replacementBox = document.createElement("textarea");
      replacementBox.value = replacement;
      replacementBox.setAttribute("aria-label", "候选修改正文");
      message.appendChild(replacementBox);
      const hint = document.createElement("small");
      hint.textContent = normalizedResult.patch_id
        ? `候选修改 patch_id=${normalizedResult.patch_id}，点击按钮后才会保存。`
        : "这是历史候选修改，可复制或放入编辑框预览。";
      message.appendChild(hint);
      const actions = document.createElement("div");
      actions.className = "script-rag-actions";
      if (normalizedResult.patch_id) {
        const applyButton = document.createElement("button");
        applyButton.className = "text-button";
        applyButton.type = "button";
        applyButton.textContent = "应用这个修改";
        applyButton.addEventListener("click", () => applyPatch(normalizedResult.patch_id));
        actions.appendChild(applyButton);
        const rejectButton = document.createElement("button");
        rejectButton.className = "text-button";
        rejectButton.type = "button";
        rejectButton.textContent = "放弃这个修改";
        rejectButton.addEventListener("click", () => rejectPatch(normalizedResult.patch_id));
        actions.appendChild(rejectButton);
      }
      const previewButton = document.createElement("button");
      previewButton.className = "text-button";
      previewButton.type = "button";
      previewButton.textContent = "放入编辑框预览";
      previewButton.addEventListener("click", () => applyReplacement(replacement));
      actions.appendChild(previewButton);
      message.appendChild(actions);
    }
    ragChat.appendChild(message);
    ragChat.scrollTop = ragChat.scrollHeight;
  }

  function resetChatHistory() {
    ragChat.innerHTML = initialChatHtml;
    ragChat.scrollTop = 0;
  }

  function renderConversationMessages(messages) {
    if (!messages.length) {
      resetChatHistory();
      return;
    }
    ragChat.innerHTML = "";
    messages.forEach((message) => {
      addMessage(message.role, message.content, {
        replacement: message.replacement || "",
        patch_id: message.patch_id || null,
      });
    });
  }

  async function initializeConversations() {
    await refreshConversationList();
    if (conversations.length) {
      await loadConversation(conversations[0].conversation_id);
      return;
    }
    await createConversation("新对话", { load: true });
  }

  async function refreshConversationList() {
    const response = await fetch(`/api/script/generations/${encodeURIComponent(generationId)}/assistant/conversations`);
    const payload = await response.json();
    if (!response.ok) {
      ragStatus.textContent = payload.error || "对话列表加载失败";
      return [];
    }
    conversations = payload.conversations || [];
    renderConversationList();
    return conversations;
  }

  function renderConversationList() {
    conversationList.innerHTML = "";
    if (!conversations.length) {
      const empty = document.createElement("p");
      empty.className = "muted-text";
      empty.textContent = "暂无历史对话";
      conversationList.appendChild(empty);
      return;
    }
    conversations.forEach((conversation) => {
      const item = document.createElement("div");
      item.className = `script-conversation-item${conversation.conversation_id === currentConversationId ? " active" : ""}`;
      const meta = document.createElement("button");
      meta.className = "script-conversation-meta text-button";
      meta.type = "button";
      meta.addEventListener("click", () => loadConversation(conversation.conversation_id));
      const title = document.createElement("strong");
      title.textContent = conversation.title || "新对话";
      const time = document.createElement("span");
      time.textContent = conversation.updated_at || conversation.created_at || "";
      const preview = document.createElement("small");
      preview.textContent = conversation.last_message_preview || "还没有消息";
      meta.append(title, time, preview);
      const deleteButton = document.createElement("button");
      deleteButton.className = "text-button";
      deleteButton.type = "button";
      deleteButton.textContent = "删除";
      deleteButton.addEventListener("click", () => deleteConversation(conversation.conversation_id));
      item.append(meta, deleteButton);
      conversationList.appendChild(item);
    });
  }

  async function createConversation(title, options) {
    const response = await fetch(`/api/script/generations/${encodeURIComponent(generationId)}/assistant/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title || "新对话" }),
    });
    const payload = await response.json();
    if (!response.ok) {
      ragStatus.textContent = payload.error || "新建对话失败";
      return null;
    }
    const conversation = payload.conversation;
    currentConversationId = conversation.conversation_id;
    conversationTitle.textContent = conversation.title || "新对话";
    await refreshConversationList();
    if (!options || options.load) {
      resetChatHistory();
    }
    return conversation;
  }

  async function ensureConversation() {
    if (currentConversationId) {
      return currentConversationId;
    }
    const conversation = await createConversation("新对话", { load: false });
    return conversation ? conversation.conversation_id : "";
  }

  async function loadConversation(conversationId) {
    const response = await fetch(
      `/api/script/generations/${encodeURIComponent(generationId)}/assistant/conversations/${encodeURIComponent(conversationId)}`
    );
    const payload = await response.json();
    if (!response.ok) {
      ragStatus.textContent = payload.error || "对话加载失败";
      return;
    }
    currentConversationId = payload.conversation.conversation_id;
    conversationTitle.textContent = payload.conversation.title || "新对话";
    renderConversationMessages(payload.messages || []);
    renderConversationList();
    conversationList.hidden = true;
    ragStatus.textContent = "";
  }

  async function deleteConversation(conversationId) {
    const response = await fetch(
      `/api/script/generations/${encodeURIComponent(generationId)}/assistant/conversations/${encodeURIComponent(conversationId)}`,
      { method: "DELETE" }
    );
    const payload = await response.json();
    if (!response.ok) {
      ragStatus.textContent = payload.error || "删除对话失败";
      return;
    }
    await refreshConversationList();
    if (currentConversationId === conversationId) {
      if (conversations.length) {
        await loadConversation(conversations[0].conversation_id);
      } else {
        await createConversation("新对话", { load: true });
      }
    }
  }

  async function loadSelectionHistory(selection) {
    const cleanSelection = selection.trim();
    if (!cleanSelection) {
      return;
    }
    const requestToken = ++historyRequestToken;
    selectionHistoryPanel.hidden = false;
    selectionHistoryPanel.textContent = "正在查询这段相关历史...";
    const response = await fetch(`/api/script/generations/${encodeURIComponent(generationId)}/assistant/selection-history`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selection: cleanSelection }),
    });
    const payload = await response.json();
    if (requestToken !== historyRequestToken) {
      return;
    }
    if (!response.ok) {
      selectionHistoryPanel.textContent = payload.error || "相关历史查询失败";
      return;
    }
    if (!payload.messages.length) {
      selectionHistoryPanel.textContent = "这段还没有相关历史。";
      return;
    }
    selectionHistoryPanel.innerHTML = "";
    payload.messages.forEach((message) => {
      const item = document.createElement("p");
      item.textContent = `${message.role === "user" ? "你" : "AI"}：${message.content}`;
      selectionHistoryPanel.appendChild(item);
    });
  }

  async function saveArticle() {
    editStatus.textContent = "正在保存...";
    const response = await fetch(`/api/script/generations/${encodeURIComponent(generationId)}/article`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ article: editor.value }),
    });
    const payload = await response.json();
    if (!response.ok) {
      editStatus.textContent = payload.error || "保存失败";
      return;
    }
    editStatus.textContent = "已保存";
    window.location.reload();
  }

  async function askAgent() {
    const instruction = ragComposer.value.trim();
    const intentHint = pendingSelectionIntent;
    const includeSelection = Boolean(intentHint && selectedSelection);
    if (!instruction) {
      ragStatus.textContent = includeSelection ? "请描述你想怎么处理这段。" : "请输入你的问题或修改意见。";
      return;
    }
    pendingSelectionIntent = "";
    ragComposer.placeholder = defaultComposerPlaceholder;
    await sendAssistantMessage(instruction, intentHint, includeSelection);
    ragComposer.value = "";
  }

  async function sendAssistantMessage(message, intentHint, includeSelection, patchId) {
    const conversationId = await ensureConversation();
    if (!conversationId) {
      return;
    }
    const body = { conversation_id: conversationId, message };
    if (intentHint) {
      body.intent_hint = intentHint;
    }
    if (includeSelection && selectedSelection) {
      body.selection = selectedSelection;
    }
    if (patchId) {
      body.patch_id = patchId;
    }
    addMessage("user", message);
    ragStatus.textContent = intentHint === "chat" ? "正在回复..." : "正在调用剧本对话助手...";
    const response = await fetch(`/api/script/generations/${encodeURIComponent(generationId)}/assist`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok && !payload.result) {
      ragStatus.textContent = payload.error || "剧本对话助手调用失败";
      return;
    }
    if (payload.conversation) {
      currentConversationId = payload.conversation.conversation_id;
      conversationTitle.textContent = payload.conversation.title || "新对话";
    }
    const result = payload.result || {};
    addMessage("assistant", result.answer || "已处理。", result);
    await refreshConversationList();
    if (result.applied) {
      ragStatus.textContent = "已保存修改，正在刷新正文...";
      window.setTimeout(() => window.location.reload(), 450);
      return;
    }
    if (response.ok) {
      ragStatus.textContent = `已参考 ${(payload.contexts || []).length} 个本地片段`;
    } else {
      ragStatus.textContent = result.answer || "剧本对话助手调用失败";
    }
  }

  function requireSelection() {
    if (selectedSelection && selectedText) {
      return true;
    }
    ragStatus.textContent = "请先在正文里选中一段。";
    return false;
  }

  function applyPatch(patchId) {
    sendAssistantMessage("应用这个修改", "apply_patch", false, patchId);
  }

  function rejectPatch(patchId) {
    sendAssistantMessage("放弃这个修改", "reject_patch", false, patchId);
  }

  function applyReplacement(replacement) {
    const selection = selectedText.trim();
    if (!replacement || !selection) {
      ragStatus.textContent = "没有可预览的替换内容。";
      return;
    }
    if (editor.hidden) {
      setEditMode(true);
    }
    const current = editor.value;
    if (!current.includes(selection)) {
      ragStatus.textContent = "编辑框里找不到原选中文本，请手动复制建议内容。";
      return;
    }
    editor.value = current.replace(selection, replacement);
    ragStatus.textContent = "已放入编辑框预览，保存前请人工检查。";
  }

  editStart.addEventListener("click", () => setEditMode(true));
  editCancel.addEventListener("click", () => {
    editor.value = editor.defaultValue;
    editStatus.textContent = "";
    setEditMode(false);
  });
  editSave.addEventListener("click", saveArticle);
  conversationNew.addEventListener("click", () => createConversation("新对话", { load: true }));
  conversationToggle.addEventListener("click", () => {
    conversationList.hidden = !conversationList.hidden;
  });
  ragAsk.addEventListener("click", askAgent);
  ragComposer.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      askAgent();
    }
  });
  selectionExplain.addEventListener("click", () => {
    if (requireSelection()) {
      sendAssistantMessage("解释这段", "explain", true);
    }
  });
  selectionReview.addEventListener("click", () => {
    if (requireSelection()) {
      sendAssistantMessage("评审这段", "review", true);
    }
  });
  selectionEdit.addEventListener("click", () => {
    if (!requireSelection()) {
      return;
    }
    ragComposer.placeholder = "请描述你想怎么改这段";
    const message = ragComposer.value.trim() || "帮我改写这段";
    ragComposer.value = "";
    sendAssistantMessage(message, "edit", true);
  });
  selectionHistory.addEventListener("click", () => {
    if (requireSelection()) {
      loadSelectionHistory(selectedSelection.text);
    }
  });
  selectionClear.addEventListener("click", clearSelection);
  display.addEventListener("mousedown", () => {
    isPointerSelecting = true;
  });
  display.addEventListener("touchstart", () => {
    isPointerSelecting = true;
  });
  display.addEventListener("keyup", updateSelectedText);
  document.addEventListener("mouseup", syncSelectionAfterPointerUp);
  document.addEventListener("touchend", syncSelectionAfterPointerUp);
  initializeConversations();
})();
