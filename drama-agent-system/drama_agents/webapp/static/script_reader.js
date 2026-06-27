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
  const initialChatHtml = ragChat.innerHTML;
  let selectedText = "";
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
      return "";
    }
    const range = selection.getRangeAt(0);
    if (!display.contains(range.commonAncestorContainer)) {
      return "";
    }
    return selection.toString().trim();
  }

  function updateSelectedText() {
    if (isPointerSelecting) {
      return;
    }
    const nextSelectedText = readDisplaySelection();
    if (!nextSelectedText) {
      return;
    }
    selectedText = nextSelectedText;
    ragComposer.value = formatSelectedQuote(selectedText);
    loadSelectionHistory(selectedText);
  }

  function syncSelectionAfterPointerUp() {
    isPointerSelecting = false;
    window.setTimeout(updateSelectedText, 0);
  }

  function addMessage(role, content, replacement) {
    const message = document.createElement("div");
    message.className = `script-rag-message ${role}`;
    const paragraph = document.createElement("p");
    paragraph.textContent = content;
    message.appendChild(paragraph);
    if (replacement) {
      const replacementBox = document.createElement("textarea");
      replacementBox.readOnly = true;
      replacementBox.value = replacement;
      message.appendChild(replacementBox);
      const hint = document.createElement("small");
      hint.textContent = "这是待确认的局部修改建议。回复“可以”或“确认修改”后会保存到正文。";
      message.appendChild(hint);
      const applyButton = document.createElement("button");
      applyButton.className = "text-button";
      applyButton.type = "button";
      applyButton.textContent = "放入编辑框预览";
      applyButton.addEventListener("click", () => applyReplacement(replacement));
      message.appendChild(applyButton);
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
      addMessage(message.role, message.content, message.replacement || "");
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

  function parseComposer() {
    const text = ragComposer.value.trim();
    const hasQuotedSelection = selectedText && text.startsWith(formatSelectedQuote(selectedText).trim());
    return {
      selection: hasQuotedSelection ? selectedText : "",
      instruction: stripSelectedQuoteFromInstruction(text, hasQuotedSelection ? selectedText : ""),
    };
  }

  function formatSelectedQuote(text) {
    return `“${text}”\n\n`;
  }

  function stripSelectedQuoteFromInstruction(text, selection) {
    const quotedSelection = formatSelectedQuote(selection).trim();
    if (quotedSelection && text.startsWith(quotedSelection)) {
      return text.slice(quotedSelection.length).trim();
    }
    return text;
  }

  async function askAgent() {
    const { selection, instruction } = parseComposer();
    if (!instruction) {
      ragStatus.textContent = selection ? "请在引号下继续输入问题或修改意见。" : "请输入你的问题或修改意见。";
      return;
    }
    addMessage("user", ragComposer.value.trim());
    ragComposer.value = "";
    ragStatus.textContent = "正在检索本地资料并调用剧本对话助手...";
    const response = await fetch(`/api/script/generations/${encodeURIComponent(generationId)}/assist`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selection, instruction }),
    });
    const payload = await response.json();
    if (!response.ok) {
      ragStatus.textContent = payload.error || "剧本对话助手调用失败";
      return;
    }
    if (payload.result && payload.result.applied) {
      ragStatus.textContent = "已保存修改，正在刷新正文...";
      addMessage("assistant", payload.result.answer || "已保存修改。", "");
      window.setTimeout(() => window.location.reload(), 450);
      return;
    }
    ragStatus.textContent = `已参考 ${payload.contexts.length} 个本地片段`;
    addMessage("assistant", payload.result.answer || "已生成建议。", payload.result.replacement || "");
  }

  function applyReplacement(replacement) {
    const selection = selectedText.trim();
    if (!replacement || !selection) {
      ragStatus.textContent = "没有可应用的替换内容。";
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
    ragStatus.textContent = "已应用到编辑框，确认后点击保存。";
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
