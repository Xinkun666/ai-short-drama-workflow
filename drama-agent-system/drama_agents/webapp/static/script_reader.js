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
  const selectionCard = root.querySelector("[data-selection-card]");
  const selectionSummary = root.querySelector("[data-selection-summary]");
  const selectionExplain = root.querySelector("[data-selection-explain]");
  const selectionReview = root.querySelector("[data-selection-review]");
  const selectionEdit = root.querySelector("[data-selection-edit]");
  const selectionClear = root.querySelector("[data-selection-clear]");
  const initialChatHtml = ragChat.innerHTML;
  const defaultComposerPlaceholder = ragComposer.getAttribute("placeholder") || "";
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
    loadSelectionHistory(selectedText);
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
    const selection = window.getSelection();
    if (selection) {
      selection.removeAllRanges();
    }
    resetChatHistory();
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

  function renderHistoryMessages(messages) {
    ragChat.innerHTML = "";
    messages.forEach((message) => {
      addMessage(message.role, message.content, {
        replacement: message.replacement || "",
        patch_id: message.patch_id || null,
      });
    });
  }

  async function loadSelectionHistory(selection) {
    const cleanSelection = selection.trim();
    if (!cleanSelection) {
      return;
    }
    const requestToken = ++historyRequestToken;
    ragStatus.textContent = "正在恢复这段的历史对话...";
    const response = await fetch(`/api/script/generations/${encodeURIComponent(generationId)}/assist/history`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selection: cleanSelection }),
    });
    const payload = await response.json();
    if (requestToken !== historyRequestToken) {
      return;
    }
    if (!response.ok) {
      ragStatus.textContent = payload.error || "历史对话恢复失败";
      return;
    }
    if (!payload.messages.length) {
      resetChatHistory();
      ragStatus.textContent = "这段还没有历史对话。";
      return;
    }
    renderHistoryMessages(payload.messages);
    ragStatus.textContent = `已恢复 ${payload.messages.length} 条历史对话`;
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
    const body = { message };
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
    const result = payload.result || {};
    addMessage("assistant", result.answer || "已处理。", result);
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
})();
