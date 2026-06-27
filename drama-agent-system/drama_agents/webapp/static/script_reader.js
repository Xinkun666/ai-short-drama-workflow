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
  const conversationList = root.querySelector("[data-conversation-list]");
  const conversationMore = root.querySelector("[data-conversation-more]");
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
  const selectionQuoteStart = "【选中的剧本文字】\n";
  const selectionQuoteEnd = "\n【你的问题或修改要求】\n";
  let currentConversationId = "";
  let conversations = [];
  let conversationsExpanded = false;
  let selectedText = "";
  let selectedSelection = null;
  let pendingSelectionIntent = "";
  let isPointerSelecting = false;
  let isSendingAssistantMessage = false;
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
    insertSelectionIntoComposer(nextSelection);
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

  function formatSelectionQuote(selection) {
    return `${selectionQuoteStart}${selection.text}${selectionQuoteEnd}`;
  }

  function setComposerSending(isSending) {
    isSendingAssistantMessage = isSending;
    ragComposer.disabled = isSending;
    ragAsk.disabled = isSending;
  }

  function selectionParagraphLabel(selection) {
    if (!selection || !selection.paragraph_id) {
      return "跨段文本";
    }
    return `第 ${selection.paragraph_id.replace("script-paragraph-", "")} 段`;
  }

  function renderSelectionAttachment(container, selection) {
    if (!selection || !selection.text) {
      return;
    }
    const attachment = document.createElement("div");
    attachment.className = "script-rag-selection-attachment";
    const label = document.createElement("strong");
    label.textContent = `已附带选区：${selectionParagraphLabel(selection)} / ${selection.text.length} 字`;
    const preview = document.createElement("span");
    const normalizedText = selection.text.replace(/\s+/g, " ").trim();
    preview.textContent = normalizedText.length > 96 ? `${normalizedText.slice(0, 96)}...` : normalizedText;
    attachment.appendChild(label);
    attachment.appendChild(preview);
    container.appendChild(attachment);
  }

  function composerHasSelectionQuote() {
    return ragComposer.value.trimStart().startsWith(selectionQuoteStart);
  }

  function parseComposerSubmission() {
    const raw = ragComposer.value.trim();
    if (!raw.startsWith(selectionQuoteStart)) {
      return { message: raw, selection: null };
    }
    const withoutStart = raw.slice(selectionQuoteStart.length);
    const boundary = withoutStart.indexOf(selectionQuoteEnd);
    if (boundary < 0) {
      return { message: raw, selection: null };
    }
    const quotedText = withoutStart.slice(0, boundary).trim();
    const message = withoutStart.slice(boundary + selectionQuoteEnd.length).trim();
    if (!quotedText || !selectedSelection) {
      return { message, selection: null };
    }
    return {
      message,
      selection: {
        ...selectedSelection,
        text: quotedText,
      },
    };
  }

  function insertSelectionIntoComposer(selection) {
    if (!selection || !selection.text) {
      return;
    }
    const previous = parseComposerSubmission();
    const preservedMessage = composerHasSelectionQuote() ? previous.message : ragComposer.value.trim();
    if (!composerHasSelectionQuote() && preservedMessage) {
      ragComposer.value = `${formatSelectionQuote(selection)}${preservedMessage}`;
    } else {
      ragComposer.value = `${formatSelectionQuote(selection)}${preservedMessage}`;
    }
    autoResizeComposer();
    ragComposer.focus();
    ragComposer.selectionStart = ragComposer.value.length;
    ragComposer.selectionEnd = ragComposer.value.length;
  }

  function removeSelectionQuoteFromComposer() {
    if (!composerHasSelectionQuote()) {
      return;
    }
    ragComposer.value = parseComposerSubmission().message;
    autoResizeComposer();
  }

  function autoResizeComposer() {
    ragComposer.style.height = "auto";
    ragComposer.style.height = `${Math.min(ragComposer.scrollHeight, 240)}px`;
  }

  function clearSelection() {
    selectedText = "";
    selectedSelection = null;
    pendingSelectionIntent = "";
    ragComposer.placeholder = defaultComposerPlaceholder;
    removeSelectionQuoteFromComposer();
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

  function addMessage(role, content, result, selection) {
    const message = document.createElement("div");
    message.className = `script-rag-message ${role}`;
    if (role === "user") {
      renderSelectionAttachment(message, selection);
    }
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
      }, message.selection || null);
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
      if (conversationMore) {
        conversationMore.hidden = true;
      }
      return;
    }
    const visibleConversations = conversationsExpanded ? conversations : conversations.slice(0, 3);
    visibleConversations.forEach((conversation) => {
      const item = document.createElement("div");
      item.className = `script-conversation-item${conversation.conversation_id === currentConversationId ? " active" : ""}`;
      const date = document.createElement("time");
      date.className = "script-conversation-date";
      date.textContent = formatConversationTime(conversation.updated_at || conversation.created_at || "");
      const title = document.createElement("strong");
      title.className = "script-conversation-title";
      title.textContent = conversation.title || "新对话";
      title.title = conversation.title || "新对话";
      title.addEventListener("click", () => loadConversation(conversation.conversation_id));
      const moreWrap = document.createElement("div");
      moreWrap.className = "script-conversation-more-wrap";
      const moreButton = document.createElement("button");
      moreButton.className = "script-conversation-kebab";
      moreButton.type = "button";
      moreButton.textContent = "...";
      const menu = document.createElement("div");
      menu.className = "script-conversation-more-menu";
      menu.hidden = true;
      const loadButton = document.createElement("button");
      loadButton.type = "button";
      loadButton.textContent = "加载对话";
      loadButton.addEventListener("click", () => loadConversation(conversation.conversation_id));
      const renameButton = document.createElement("button");
      renameButton.type = "button";
      renameButton.textContent = "修改标题";
      renameButton.addEventListener("click", () => renameConversation(conversation));
      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.textContent = "删除对话";
      deleteButton.addEventListener("click", () => deleteConversation(conversation.conversation_id));
      menu.append(loadButton, renameButton, deleteButton);
      moreButton.addEventListener("click", (event) => {
        event.stopPropagation();
        root.querySelectorAll(".script-conversation-more-menu").forEach((node) => {
          if (node !== menu) {
            node.hidden = true;
          }
        });
        menu.hidden = !menu.hidden;
      });
      moreWrap.append(moreButton, menu);
      item.append(date, title, moreWrap);
      conversationList.appendChild(item);
    });
    if (conversationMore) {
      conversationMore.hidden = conversations.length <= 3;
      conversationMore.textContent = conversationsExpanded ? "⌃ 收起历史" : "⌄ 更多历史";
    }
  }

  function formatConversationTime(value) {
    return String(value || "").replace("T", " ").slice(0, 16);
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
    ragStatus.textContent = "";
  }

  async function renameConversation(conversation) {
    const nextTitle = window.prompt("修改标题", conversation.title || "新对话");
    if (!nextTitle || !nextTitle.trim()) {
      return;
    }
    const response = await fetch(
      `/api/script/generations/${encodeURIComponent(generationId)}/assistant/conversations/${encodeURIComponent(conversation.conversation_id)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: nextTitle.trim() }),
      }
    );
    const payload = await response.json();
    if (!response.ok) {
      ragStatus.textContent = payload.error || "标题修改失败";
      return;
    }
    if (currentConversationId === conversation.conversation_id) {
      conversationTitle.textContent = payload.conversation.title || "新对话";
    }
    await refreshConversationList();
  }

  async function deleteConversation(conversationId) {
    if (!window.confirm("确定删除这条历史对话吗？")) {
      return;
    }
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
    if (isSendingAssistantMessage) {
      return;
    }
    const submission = parseComposerSubmission();
    const instruction = submission.message.trim();
    const intentHint = pendingSelectionIntent;
    const selectionForMessage = submission.selection || (intentHint && selectedSelection ? selectedSelection : null);
    if (!instruction) {
      ragStatus.textContent = selectionForMessage ? "请描述你想怎么处理这段。" : "请输入你的问题或修改意见。";
      return;
    }
    pendingSelectionIntent = "";
    ragComposer.placeholder = defaultComposerPlaceholder;
    ragComposer.value = "";
    autoResizeComposer();
    await sendAssistantMessage(instruction, intentHint, selectionForMessage);
  }

  async function sendAssistantMessage(message, intentHint, selectionForMessage, patchId) {
    if (isSendingAssistantMessage) {
      return;
    }
    setComposerSending(true);
    try {
      const conversationId = await ensureConversation();
      if (!conversationId) {
        return;
      }
      const body = { conversation_id: conversationId, message };
      if (intentHint) {
        body.intent_hint = intentHint;
      }
      if (selectionForMessage) {
        body.selection = selectionForMessage;
      }
      if (patchId) {
        body.patch_id = patchId;
      }
      addMessage("user", message, null, selectionForMessage);
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
    } catch (error) {
      ragStatus.textContent = "剧本对话助手调用失败，请稍后重试。";
    } finally {
      setComposerSending(false);
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
    sendAssistantMessage("应用这个修改", "apply_patch", null, patchId);
  }

  function rejectPatch(patchId) {
    sendAssistantMessage("放弃这个修改", "reject_patch", null, patchId);
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
  if (conversationMore) {
    conversationMore.addEventListener("click", () => {
      conversationsExpanded = !conversationsExpanded;
      renderConversationList();
    });
  }
  ragAsk.addEventListener("click", askAgent);
  ragComposer.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      askAgent();
    } else if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      askAgent();
    }
  });
  ragComposer.addEventListener("input", autoResizeComposer);
  selectionExplain.addEventListener("click", () => {
    if (requireSelection()) {
      sendAssistantMessage("解释这段", "explain", selectedSelection);
    }
  });
  selectionReview.addEventListener("click", () => {
    if (requireSelection()) {
      sendAssistantMessage("评审这段", "review", selectedSelection);
    }
  });
  selectionEdit.addEventListener("click", () => {
    if (!requireSelection()) {
      return;
    }
    ragComposer.placeholder = "请描述你想怎么改这段";
    const message = parseComposerSubmission().message.trim() || "帮我改写这段";
    ragComposer.value = "";
    autoResizeComposer();
    sendAssistantMessage(message, "edit", selectedSelection);
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
