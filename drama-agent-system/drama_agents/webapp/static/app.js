const state = {
  sources: [],
  records: [],
  scriptGenerations: [],
  visualSubjects: [],
  visualSubjectGroups: [],
  visualScriptSubjects: [],
  visualRejectedCandidates: [],
  selectedVisualScriptId: "",
  visualSubjectQuery: "",
  visualMode: "all",
  visualScriptStage: "list",
  visualScriptStatuses: {},
  visualScriptSubjectCounts: {},
  timelineSources: [],
  selectedTimelineIds: new Set(),
  selected: null,
  parsing: false,
  parseTimers: [],
};

const elements = {
  navTabs: document.querySelectorAll("[data-view-tab]"),
  panels: document.querySelectorAll("[data-view-panel]"),
  commandBox: document.querySelector("#commandBox"),
  commandInput: document.querySelector("#commandInput"),
  fileInput: document.querySelector("#fileInput"),
  uploadButton: document.querySelector("#uploadButton"),
  parseButton: document.querySelector("#parseButton"),
  refreshButton: document.querySelector("#refreshButton"),
  sourceHint: document.querySelector("#sourceHint"),
  recordsList: document.querySelector("#recordsList"),
  scriptTopicInput: document.querySelector("#scriptTopicInput"),
  scriptStartYearInput: document.querySelector("#scriptStartYearInput"),
  scriptEndYearInput: document.querySelector("#scriptEndYearInput"),
  timelinePicker: document.querySelector("#timelinePicker"),
  timelineToggle: document.querySelector("#timelineToggle"),
  timelinePopover: document.querySelector("#timelinePopover"),
  closeTimelinePicker: document.querySelector("#closeTimelinePicker"),
  timelineSourceList: document.querySelector("#timelineSourceList"),
  selectedTimelineCount: document.querySelector("#selectedTimelineCount"),
  scriptGenerateButton: document.querySelector("#scriptGenerateButton"),
  scriptHint: document.querySelector("#scriptHint"),
  refreshScriptRecordsButton: document.querySelector("#refreshScriptRecordsButton"),
  scriptRecordsList: document.querySelector("#scriptRecordsList"),
  scriptResultPanel: document.querySelector("#scriptResultPanel"),
  scriptResultTitle: document.querySelector("#scriptResultTitle"),
  scriptViewerTabs: document.querySelectorAll("[data-script-viewer]"),
  scriptViewerPanels: document.querySelectorAll("[data-script-viewer-panel]"),
  scriptScenes: document.querySelector("#scriptScenes"),
  scriptReview: document.querySelector("#scriptReview"),
  scriptSubjects: document.querySelector("#scriptSubjects"),
  scriptMapShots: document.querySelector("#scriptMapShots"),
  scriptMatchedEvents: document.querySelector("#scriptMatchedEvents"),
  sceneModuleTabs: document.querySelectorAll("[data-scene-tab]"),
  sceneModulePanels: document.querySelectorAll("[data-scene-panel]"),
  visualModeTabs: document.querySelectorAll("[data-visual-mode]"),
  visualScriptBackButtons: document.querySelectorAll("[data-visual-script-back]"),
  visualWorkbenchGrid: document.querySelector("#visualWorkbenchGrid"),
  visualScriptFileInput: document.querySelector("#visualScriptFileInput"),
  visualUploadButton: document.querySelector("#visualUploadButton"),
  visualScriptSelect: document.querySelector("#visualScriptSelect"),
  visualExtractButton: document.querySelector("#visualExtractButton"),
  visualStatus: document.querySelector("#visualStatus"),
  visualCurrentScript: document.querySelector("#visualCurrentScript"),
  refreshVisualSubjectsButton: document.querySelector("#refreshVisualSubjectsButton"),
  refreshVisualScriptSubjectsButton: document.querySelector("#refreshVisualScriptSubjectsButton"),
  visualScriptList: document.querySelector("#visualScriptList"),
  visualSelectedScriptTitle: document.querySelector("#visualSelectedScriptTitle"),
  visualSubjectSearchInput: document.querySelector("#visualSubjectSearchInput"),
  visualSubjectPool: document.querySelector("#visualSubjectPool"),
  visualScriptSubjects: document.querySelector("#visualScriptSubjects"),
  visualRejectedCandidates: document.querySelector("#visualRejectedCandidates"),
};

function setActiveView(viewName) {
  elements.navTabs.forEach((tab) => {
    const isActive = tab.dataset.viewTab === viewName;
    tab.classList.toggle("active", isActive);
    tab.setAttribute("aria-selected", isActive ? "true" : "false");
  });
  elements.panels.forEach((panel) => {
    const isActive = panel.dataset.viewPanel === viewName;
    panel.hidden = !isActive;
    panel.classList.toggle("active", isActive);
  });
  if (viewName === "script") {
    Promise.all([loadTimelineSources(), loadScriptGenerations()]).catch((error) => {
      elements.scriptHint.textContent = error.message;
    });
  } else if (viewName === "scene") {
    closeTimelinePicker();
    Promise.all([loadScriptGenerations(), loadVisualSubjects()]).catch((error) => {
      setVisualStatus(error.message, "error");
    });
  } else {
    closeTimelinePicker();
  }
}

function viewNameFromHash() {
  const viewName = window.location.hash.replace("#", "");
  return ["materials", "script", "scene"].includes(viewName) ? viewName : "";
}

function applyHashView() {
  const viewName = viewNameFromHash();
  if (viewName) {
    setActiveView(viewName);
  }
}

function setCommandState(kind, text) {
  elements.commandBox.classList.remove("is-parsing", "is-success", "is-error", "is-searching");
  if (kind) {
    elements.commandBox.classList.add(`is-${kind}`);
  }
  if (text !== undefined) {
    elements.commandInput.value = text;
  }
}

function setSelectedSource(source) {
  state.selected = source;
  elements.parseButton.disabled = !source || state.parsing;
  if (source) {
    setCommandState("", source.name);
    elements.sourceHint.textContent = `${source.relative_path} · ${source.size_mb} MB`;
  } else {
    setCommandState("", "");
    elements.sourceHint.textContent = "上传或选择一本材料后开始解析。";
  }
}

function clearParseTimers() {
  state.parseTimers.forEach((timer) => window.clearTimeout(timer));
  state.parseTimers = [];
}

function scheduleParseSteps(bookName) {
  clearParseTimers();
  const steps = [
    `正在解析：读取 ${bookName}`,
    "正在解析：识别目录和章节边界",
    "正在解析：拆分章节文件与 Markdown",
    "正在解析：调用 DeepSeek 精提取章节内容",
    "正在解析：格式化章节阅读器",
    "正在解析：统计全书字数和生成记录",
  ];
  steps.forEach((step, index) => {
    state.parseTimers.push(
      window.setTimeout(() => {
        if (state.parsing) {
          setCommandState("parsing", step);
        }
      }, index * 1300)
    );
  });
}

async function loadSources() {
  const response = await fetch("/api/sources");
  const payload = await response.json();
  state.sources = payload.sources || [];
  if (!state.selected && state.sources.length) {
    setSelectedSource(state.sources[0]);
  } else if (state.selected) {
    const nextSelected = state.sources.find((source) => source.relative_path === state.selected.relative_path);
    setSelectedSource(nextSelected || null);
  }
}

async function loadRecords() {
  const response = await fetch("/api/records");
  const payload = await response.json();
  state.records = payload.records || [];
  renderRecords(state.records);
}

async function loadTimelineSources() {
  const response = await fetch("/api/script/timeline-sources");
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "时间线列表加载失败");
  }
  state.timelineSources = payload.timeline_sources || [];
  const availableIds = new Set(state.timelineSources.map((source) => source.record_id));
  state.selectedTimelineIds = new Set([...state.selectedTimelineIds].filter((recordId) => availableIds.has(recordId)));
  renderTimelineSources();
}

async function loadScriptGenerations() {
  const response = await fetch("/api/script/generations");
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "剧本记录加载失败");
  }
  state.scriptGenerations = payload.generations || [];
  renderScriptGenerations();
  renderVisualScriptSelect();
}

function filteredRecords() {
  const query = elements.commandInput.value.trim().toLowerCase();
  if (!query || state.parsing) {
    return state.records;
  }
  return state.records.filter((record) => record.book_name.toLowerCase().includes(query));
}

function renderRecords(records) {
  if (!records.length) {
    elements.recordsList.innerHTML = '<div class="empty-record">暂无解析记录</div>';
    return;
  }
  elements.recordsList.innerHTML = records
    .map((record) => {
      return `
        <div class="record-row" data-record-id="${escapeHtml(record.record_id)}">
          <div>
            <span class="record-label">解析时间</span>
            <span class="record-value">${escapeHtml(record.parsed_at)}</span>
          </div>
          <div>
            <span class="record-label">书名</span>
            <div class="record-title-editor" data-title-editor="${escapeHtml(record.record_id)}">
              <span class="record-value record-title-text">${escapeHtml(record.book_name)}</span>
              <input
                class="record-title-input"
                type="text"
                value="${escapeHtml(record.book_name)}"
                aria-label="编辑书名"
                hidden
              >
              <button class="record-mini-button" type="button" data-edit-title="${escapeHtml(record.record_id)}">改名</button>
              <button class="record-mini-button save" type="button" data-save-title="${escapeHtml(record.record_id)}" hidden>保存</button>
              <button class="record-mini-button ghost" type="button" data-cancel-title="${escapeHtml(record.record_id)}" hidden>取消</button>
            </div>
          </div>
          <div>
            <span class="record-label">全书字数</span>
            <span class="record-value">${formatNumber(record.total_words)}</span>
          </div>
          <div>
            <span class="record-label">章节数</span>
            <span class="record-value">${record.chapter_count} 章</span>
          </div>
          <div>
            <span class="record-label">精提取</span>
            <span class="record-value">${readerStatusText(record)}</span>
          </div>
          <a class="record-action" href="${record.detail_url}">详情</a>
          <button class="record-action delete" type="button" data-delete="${escapeHtml(record.record_id)}">删除</button>
        </div>
      `;
    })
    .join("");

  document.querySelectorAll("[data-delete]").forEach((button) => {
    button.addEventListener("click", () => deleteRecord(button.dataset.delete));
  });
  document.querySelectorAll("[data-edit-title]").forEach((button) => {
    button.addEventListener("click", () => startTitleEdit(button.dataset.editTitle));
  });
  document.querySelectorAll("[data-save-title]").forEach((button) => {
    button.addEventListener("click", () => saveTitleEdit(button.dataset.saveTitle));
  });
  document.querySelectorAll("[data-cancel-title]").forEach((button) => {
    button.addEventListener("click", () => cancelTitleEdit(button.dataset.cancelTitle));
  });
  document.querySelectorAll(".record-title-input").forEach((input) => {
    input.addEventListener("keydown", (event) => {
      const recordId = input.closest("[data-record-id]").dataset.recordId;
      if (event.key === "Enter") {
        event.preventDefault();
        saveTitleEdit(recordId);
      }
      if (event.key === "Escape") {
        event.preventDefault();
        cancelTitleEdit(recordId);
      }
    });
  });
}

function renderTimelineSources() {
  updateSelectedTimelineCount();
  if (!state.timelineSources.length) {
    elements.timelineSourceList.innerHTML = '<div class="empty-timeline-source">暂无可选时间线</div>';
    elements.scriptHint.textContent = "请先在材料详情页生成书籍时间线。";
    return;
  }
  elements.timelineSourceList.innerHTML = state.timelineSources
    .map((source) => {
      const checked = state.selectedTimelineIds.has(source.record_id) ? "checked" : "";
      return `
        <label class="timeline-source-item">
          <input type="checkbox" value="${escapeHtml(source.record_id)}" ${checked}>
          <span>
            <strong>${escapeHtml(source.book_name)}</strong>
            <em>${formatNumber(source.timeline_event_count)} 个时间线模块</em>
          </span>
          <a href="${escapeHtml(source.timeline_url)}" target="_blank" rel="noreferrer">查看</a>
        </label>
      `;
    })
    .join("");
  elements.timelineSourceList.querySelectorAll("input[type='checkbox']").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.selectedTimelineIds.add(checkbox.value);
      } else {
        state.selectedTimelineIds.delete(checkbox.value);
      }
      updateSelectedTimelineCount();
      updateScriptHint();
    });
  });
  updateScriptHint();
}

function renderScriptGenerations() {
  if (!state.scriptGenerations.length) {
    elements.scriptRecordsList.innerHTML = '<div class="empty-record">暂无剧本记录</div>';
    return;
  }
  elements.scriptRecordsList.innerHTML = state.scriptGenerations
    .map((record) => {
      return `
        <div class="script-record-row" data-script-generation-id="${escapeHtml(record.generation_id)}">
          <div>
            <span class="record-label">生成时间</span>
            <span class="record-value">${escapeHtml(record.created_at || "")}</span>
          </div>
          <div>
            <span class="record-label">主题名</span>
            <span class="record-value">${escapeHtml(record.topic || "")}</span>
          </div>
          <div>
            <span class="record-label">时间范围</span>
            <span class="record-value">${escapeHtml(record.time_range || "")}</span>
          </div>
          <a class="record-action" href="/script-generations/${encodeURIComponent(record.generation_id)}/script">剧本</a>
          <button class="record-action delete" type="button" data-script-delete="${escapeHtml(record.generation_id)}">删除</button>
        </div>
      `;
    })
    .join("");
  elements.scriptRecordsList.querySelectorAll("[data-script-delete]").forEach((button) => {
    button.addEventListener("click", () => deleteScriptGeneration(button.dataset.scriptDelete));
  });
}

async function uploadSelectedFile() {
  const file = elements.fileInput.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  setCommandState("", "正在上传文件...");
  elements.parseButton.disabled = true;

  const response = await fetch("/api/upload", { method: "POST", body: formData });
  const payload = await response.json();
  elements.fileInput.value = "";
  if (!response.ok) {
    setCommandState("error", payload.error || "上传失败");
    return;
  }
  await loadSources();
  setSelectedSource(payload.source);
  elements.sourceHint.textContent = "上传成功，可以开始解析。";
}

async function loadVisualSubjects() {
  const response = await fetch("/api/visual/subjects");
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "主体池加载失败");
  }
  state.visualSubjects = payload.subjects || [];
  state.visualSubjectGroups = payload.groups || [];
  renderVisualSubjectPool();
}

function renderVisualScriptSelect() {
  if (!elements.visualScriptSelect) return;
  if (!state.scriptGenerations.length) {
    elements.visualScriptSelect.innerHTML = '<option value="">暂无已有剧本</option>';
    state.selectedVisualScriptId = "";
    updateVisualCurrentScript();
    renderVisualScriptList();
    renderVisualScriptSubjects();
    return;
  }
  const existingSelected = state.scriptGenerations.some((script) => script.generation_id === state.selectedVisualScriptId);
  if (!existingSelected) {
    state.selectedVisualScriptId = state.scriptGenerations[0].generation_id;
  }
  elements.visualScriptSelect.innerHTML = state.scriptGenerations
    .map((script) => {
      const selected = script.generation_id === state.selectedVisualScriptId ? "selected" : "";
      return `<option value="${escapeHtml(script.generation_id)}" ${selected}>${escapeHtml(script.script_title || script.topic || script.generation_id)}</option>`;
    })
    .join("");
  updateVisualCurrentScript();
  renderVisualScriptList();
  if (state.selectedVisualScriptId) {
    loadScriptVisualSubjects(state.selectedVisualScriptId).catch((error) => setVisualStatus(error.message, "error"));
  }
}

function groupedVisualSubjectsForDisplay() {
  const query = state.visualSubjectQuery.trim().toLowerCase();
  const filtered = state.visualSubjects.filter((subject) => {
    if (!query) return true;
    const haystack = [
      subject.canonical_name,
      subject.short_description,
      subject.subject_type,
      subject.pinyin_key,
      ...(subject.aliases || []),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
  const grouped = new Map();
  filtered.forEach((subject) => {
    const letter = String(subject.first_letter || "#").toUpperCase();
    if (!grouped.has(letter)) {
      grouped.set(letter, []);
    }
    grouped.get(letter).push(subject);
  });
  return [...grouped.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([letter, subjects]) => ({
      letter,
      subjects: subjects.sort((left, right) =>
        `${left.pinyin_key || ""}${left.canonical_name || ""}`.localeCompare(
          `${right.pinyin_key || ""}${right.canonical_name || ""}`
        )
      ),
    }));
}

function visualStatusLabel(status) {
  const labels = {
    not_parsed: "未解析",
    parsing: "解析中",
    parsed: "已解析",
    failed: "解析失败",
  };
  return labels[status] || "未解析";
}

function visualScriptParseStatus(generationId) {
  return state.visualScriptStatuses[generationId] || "not_parsed";
}

function visualSubjectTypeLabel(type) {
  const labels = {
    species: "物种",
    group: "人群",
    character: "角色",
    civilization_group: "族群",
    symbolic_entity: "视觉符号",
    organization: "组织",
  };
  return labels[type] || type || "未分类";
}

function visualImportanceLabel(importance) {
  const score = Number(importance || 0);
  if (score >= 5) return "核心主角";
  if (score >= 4) return "对照主体";
  if (score >= 3) return "重点主体";
  return "辅助主体";
}

function renderVisualSubjectPool() {
  if (!elements.visualSubjectPool) return;
  const groups = groupedVisualSubjectsForDisplay();
  if (!state.visualSubjects.length) {
    elements.visualSubjectPool.innerHTML = `
      <div class="visual-empty-state">
        <strong>暂无主体</strong>
        <span>选择剧本并点击“解析主体”后，系统会自动识别需要保持视觉一致的角色、人群和物种。</span>
      </div>
    `;
    return;
  }
  if (!groups.length) {
    elements.visualSubjectPool.innerHTML = `
      <div class="visual-empty-state">
        <strong>没有匹配的主体</strong>
        <span>换一个关键词试试，例如智人、尼安德特人。</span>
      </div>
    `;
    return;
  }
  elements.visualSubjectPool.innerHTML = groups
    .map((group) => {
      const cards = (group.subjects || [])
        .map((subject) => renderVisualSubjectListItem(subject))
        .join("");
      return `
        <section class="visual-subject-group">
          <h3>${escapeHtml(group.letter)}</h3>
          <div class="visual-subject-card-list">${cards}</div>
        </section>
      `;
    })
    .join("");
  bindVisualSubjectActions(elements.visualSubjectPool);
}

function bindVisualSubjectActions(root) {
  root.querySelectorAll("[data-visual-subject-card]").forEach((card) => {
    card.addEventListener("click", (event) => {
      if (event.target.closest("a")) return;
      window.location.href = visualSubjectDetailUrl(card.dataset.visualSubjectCard);
    });
  });
}

function visualSubjectDetailUrl(subjectId) {
  return `/visual/subjects/${encodeURIComponent(subjectId || "")}`;
}

function renderVisualSubjectListItem(subject) {
  return renderVisualSubjectCard(subject);
}

function renderVisualScriptSubjectItem(subject) {
  return `
    <article class="visual-script-subject-row">
      <div class="visual-script-subject-copy">
        <strong>${escapeHtml(subject.canonical_name)}</strong>
        <em>${escapeHtml(visualImportanceLabel(subject.importance))} · 重要度 ${escapeHtml(String(subject.importance || 0))}</em>
        <p>${escapeHtml(subject.role_in_script || "")}</p>
      </div>
      <a href="${visualSubjectDetailUrl(subject.subject_id)}">详情</a>
    </article>
  `;
}

function sortVisualScriptSubjectsByImportance(subjects) {
  return [...subjects].sort((left, right) => {
    const importanceGap = Number(right.importance || 0) - Number(left.importance || 0);
    if (importanceGap !== 0) return importanceGap;
    return `${left.pinyin_key || ""}${left.canonical_name || ""}`.localeCompare(
      `${right.pinyin_key || ""}${right.canonical_name || ""}`
    );
  });
}

function renderVisualSubjectCard(subject) {
  const identityStatus = subject.has_visual_identity ? "已有视觉设定" : "待补视觉设定";
  const anchorStatus = subject.has_anchor_asset ? "已生成锚点图" : "未生成锚点图";
  return `
    <article class="visual-subject-card" data-visual-subject-card="${escapeHtml(subject.subject_id)}" tabindex="0">
      <div class="visual-subject-card-head">
        <strong>${escapeHtml(subject.canonical_name || "")}</strong>
        <span>${escapeHtml(visualSubjectTypeLabel(subject.subject_type || ""))}</span>
      </div>
      <p>${escapeHtml(subject.short_description || "暂无描述")}</p>
      <div class="visual-subject-status-row">
        <span>${identityStatus}</span>
        <span>${anchorStatus}</span>
        <span>${formatNumber(subject.script_count || 0)} 个剧本</span>
      </div>
      <a href="${visualSubjectDetailUrl(subject.subject_id)}">详情</a>
    </article>
  `;
}

function renderVisualScriptList() {
  if (!elements.visualScriptList) return;
  if (!state.scriptGenerations.length) {
    elements.visualScriptList.innerHTML = `
      <div class="visual-empty-state compact">
        <strong>暂无剧本</strong>
        <span>先在“剧本生成”里生成剧本，或从顶部上传剧本文本。</span>
      </div>
    `;
    return;
  }
  elements.visualScriptList.innerHTML = state.scriptGenerations
    .map((script) => {
      const isActive = script.generation_id === state.selectedVisualScriptId;
      const status = visualScriptParseStatus(script.generation_id);
      const count = state.visualScriptSubjectCounts[script.generation_id] || 0;
      const label = status === "parsed" ? `已识别 ${count} 个主体` : visualStatusLabel(status);
      return `
        <article class="visual-script-item ${isActive ? "active" : ""}">
          <div>
            <strong>${escapeHtml(script.script_title || script.topic || script.generation_id)}</strong>
            <span>${escapeHtml(script.created_at || "")}</span>
            <em data-status="${escapeHtml(status)}">${escapeHtml(label)}</em>
          </div>
          <button class="visual-script-open" type="button" data-visual-script="${escapeHtml(script.generation_id)}">主体</button>
        </article>
      `;
    })
    .join("");
  elements.visualScriptList.querySelectorAll("[data-visual-script]").forEach((button) => {
    button.addEventListener("click", () => selectVisualScript(button.dataset.visualScript));
  });
}

function selectVisualScript(generationId) {
  if (!generationId) return;
  state.selectedVisualScriptId = generationId;
  elements.visualScriptSelect.value = generationId;
  setVisualSubjectMode("scripts", { scriptStage: "subjects" });
  updateVisualCurrentScript();
  renderVisualScriptList();
  loadScriptVisualSubjects(generationId).catch((error) => setVisualStatus(error.message, "error"));
}

function setVisualSubjectMode(mode, options = {}) {
  const nextMode = mode === "scripts" ? "scripts" : "all";
  state.visualMode = nextMode;
  if (nextMode === "scripts") {
    const scriptStage = options.scriptStage || "list";
    setVisualScriptStage(scriptStage);
  }
  if (elements.visualWorkbenchGrid) {
    elements.visualWorkbenchGrid.dataset.visualCurrentMode = nextMode;
  }
  elements.visualModeTabs.forEach((tab) => {
    const isActive = tab.dataset.visualMode === nextMode;
    tab.classList.toggle("active", isActive);
    tab.setAttribute("aria-selected", isActive ? "true" : "false");
  });
  renderVisualSubjectPool();
  renderVisualScriptList();
  renderVisualScriptSubjects();
}

function setVisualScriptStage(stage) {
  const nextStage = stage === "subjects" ? "subjects" : "list";
  state.visualScriptStage = nextStage;
  if (elements.visualWorkbenchGrid) {
    elements.visualWorkbenchGrid.dataset.visualScriptStage = nextStage;
  }
}

function showVisualScriptList() {
  setVisualSubjectMode("scripts", { scriptStage: "list" });
}

async function extractVisualSubjectsFromScript() {
  if (!state.selectedVisualScriptId) {
    setVisualStatus("请先选择已有剧本。", "error");
    return;
  }
  elements.visualExtractButton.disabled = true;
  state.visualScriptStatuses[state.selectedVisualScriptId] = "parsing";
  renderVisualScriptList();
  setVisualStatus("解析中：筛选需要跨镜头保持一致的角色、人群、物种和核心视觉主体。", "loading");
  try {
    const response = await fetch("/api/visual/subjects/extract-from-script", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ generation_id: state.selectedVisualScriptId }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "主体解析失败");
    }
    state.visualScriptSubjects = payload.subjects || [];
    state.visualRejectedCandidates = payload.rejected_candidates || [];
    state.visualScriptStatuses[state.selectedVisualScriptId] = "parsed";
    state.visualScriptSubjectCounts[state.selectedVisualScriptId] = payload.script_subject_count || state.visualScriptSubjects.length;
    renderVisualScriptSubjects(payload.generation);
    renderVisualRejectedCandidates();
    await loadVisualSubjects();
    renderVisualScriptList();
    setVisualStatus(`已解析：识别 ${payload.script_subject_count || 0} 个视觉主体。`, "success");
  } catch (error) {
    state.visualScriptStatuses[state.selectedVisualScriptId] = "failed";
    renderVisualScriptList();
    setVisualStatus(error.message, "error");
  } finally {
    elements.visualExtractButton.disabled = false;
  }
}

async function uploadVisualScript() {
  const file = elements.visualScriptFileInput.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  elements.visualUploadButton.disabled = true;
  setVisualStatus("正在上传剧本并解析主体...", "loading");
  try {
    const response = await fetch("/api/visual/subjects/extract-from-upload", { method: "POST", body: formData });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "上传解析失败");
    }
    state.selectedVisualScriptId = payload.generation.generation_id;
    state.visualScriptSubjects = payload.subjects || [];
    state.visualRejectedCandidates = payload.rejected_candidates || [];
    state.visualScriptStatuses[state.selectedVisualScriptId] = "parsed";
    state.visualScriptSubjectCounts[state.selectedVisualScriptId] = payload.script_subject_count || state.visualScriptSubjects.length;
    await loadScriptGenerations();
    await loadVisualSubjects();
    renderVisualScriptSubjects(payload.generation);
    renderVisualRejectedCandidates();
    setVisualStatus(`上传解析完成：识别 ${payload.script_subject_count || 0} 个视觉主体。`, "success");
  } catch (error) {
    setVisualStatus(error.message, "error");
  } finally {
    elements.visualScriptFileInput.value = "";
    elements.visualUploadButton.disabled = false;
  }
}

async function loadScriptVisualSubjects(generationId) {
  if (!generationId) {
    state.visualScriptSubjects = [];
    renderVisualScriptSubjects();
    return;
  }
  const response = await fetch(`/api/script/generations/${encodeURIComponent(generationId)}/visual-subjects`);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "剧本主体读取失败");
  }
  state.visualScriptSubjects = payload.subjects || [];
  state.visualScriptStatuses[generationId] = payload.status || (state.visualScriptSubjects.length ? "parsed" : "not_parsed");
  state.visualScriptSubjectCounts[generationId] = state.visualScriptSubjects.length;
  renderVisualScriptList();
  renderVisualScriptSubjects(payload.generation);
  renderVisualRejectedCandidates();
}

function renderVisualScriptSubjects(generation = null) {
  if (!elements.visualScriptSubjects) return;
  const script = generation || currentVisualScript();
  if (!script) {
    elements.visualScriptSubjects.innerHTML = `
      <div class="visual-empty-state compact">
        <strong>未选择剧本</strong>
        <span>从左侧选择一个剧本后查看它的主体解析结果。</span>
      </div>
    `;
    return;
  }
  const subjects = sortVisualScriptSubjectsByImportance(state.visualScriptSubjects || []);
  const status = visualScriptParseStatus(script.generation_id);
  const rows = subjects
    .map((subject) => renderVisualScriptSubjectItem(subject))
    .join("");
  elements.visualScriptSubjects.innerHTML = `
    <article class="visual-script-summary">
      <strong>${escapeHtml(script.script_title || script.topic || "")}</strong>
      <span>${escapeHtml(script.created_at || "")}</span>
      <em>${escapeHtml(status === "parsed" ? `本剧本识别出 ${subjects.length} 个主体` : visualStatusLabel(status))}</em>
    </article>
    ${rows || '<div class="visual-empty-state compact"><strong>还没有解析主体</strong><span>点击顶部“解析主体”后，这里会显示本剧本中的角色、人群和物种。</span></div>'}
  `;
  bindVisualSubjectActions(elements.visualScriptSubjects);
}

function renderVisualRejectedCandidates() {
  if (!elements.visualRejectedCandidates) return;
  if (!state.visualRejectedCandidates.length) {
    elements.visualRejectedCandidates.innerHTML = '<span class="visual-empty-inline">暂无被拒绝候选。</span>';
    return;
  }
  elements.visualRejectedCandidates.innerHTML = state.visualRejectedCandidates
    .map((candidate) => {
      return `<div class="visual-rejected-item"><strong>${escapeHtml(candidate.name)}</strong><span>${escapeHtml(candidate.reason)}</span></div>`;
    })
    .join("");
}

function setSceneBuilderTab(tabName) {
  const nextTab = ["subjects", "scenes", "storyboard"].includes(tabName) ? tabName : "subjects";
  elements.sceneModuleTabs.forEach((tab) => {
    const isActive = tab.dataset.sceneTab === nextTab;
    tab.classList.toggle("active", isActive);
    tab.setAttribute("aria-selected", isActive ? "true" : "false");
  });
  elements.sceneModulePanels.forEach((panel) => {
    const isActive = panel.dataset.scenePanel === nextTab;
    panel.hidden = !isActive;
    panel.classList.toggle("active", isActive);
  });
}

function setVisualStatus(message, kind = "") {
  if (!elements.visualStatus) return;
  elements.visualStatus.textContent = message;
  elements.visualStatus.dataset.status = kind;
}

function currentVisualScript() {
  return state.scriptGenerations.find((script) => script.generation_id === state.selectedVisualScriptId) || null;
}

function updateVisualCurrentScript() {
  if (!elements.visualCurrentScript) return;
  const script = currentVisualScript();
  const title = script ? script.script_title || script.topic || script.generation_id : "未选择";
  elements.visualCurrentScript.textContent = `当前剧本：${title}`;
  if (elements.visualSelectedScriptTitle) {
    elements.visualSelectedScriptTitle.textContent = title;
  }
}

async function parseSelectedSource() {
  if (!state.selected || state.parsing) return;
  state.parsing = true;
  elements.parseButton.disabled = true;
  elements.uploadButton.disabled = true;
  scheduleParseSteps(state.selected.name);

  try {
    const response = await fetch("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ relative_path: state.selected.relative_path }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "解析失败");
    }
    clearParseTimers();
    const record = payload.record;
    setCommandState("success", `解析成功：${record.book_name}`);
    elements.sourceHint.textContent = `全书 ${formatNumber(record.total_words)} 字 · ${record.chapter_count} 章 · 精提取 ${record.refined_chapter_count || 0} 章 · ${record.parsed_at}`;
    await loadRecords();
  } catch (error) {
    clearParseTimers();
    setCommandState("error", error.message);
    elements.sourceHint.textContent = "解析失败，请检查材料文件。";
  } finally {
    state.parsing = false;
    elements.uploadButton.disabled = false;
    elements.parseButton.disabled = !state.selected;
  }
}

async function deleteRecord(recordId) {
  const response = await fetch(`/api/records/${encodeURIComponent(recordId)}`, { method: "DELETE" });
  if (response.ok) {
    await loadRecords();
    setCommandState("", elements.commandInput.value);
    elements.sourceHint.textContent = "解析记录已删除。";
  }
}

function titleEditor(recordId) {
  return document.querySelector(`[data-title-editor="${cssEscape(recordId)}"]`);
}

function startTitleEdit(recordId) {
  const editor = titleEditor(recordId);
  if (!editor) return;
  const input = editor.querySelector(".record-title-input");
  editor.classList.add("is-editing");
  editor.querySelector(".record-title-text").hidden = true;
  input.hidden = false;
  editor.querySelector("[data-edit-title]").hidden = true;
  editor.querySelector("[data-save-title]").hidden = false;
  editor.querySelector("[data-cancel-title]").hidden = false;
  input.focus();
  input.select();
}

function cancelTitleEdit(recordId) {
  const editor = titleEditor(recordId);
  const record = state.records.find((item) => item.record_id === recordId);
  if (!editor || !record) return;
  editor.querySelector(".record-title-input").value = record.book_name;
  finishTitleEdit(editor);
}

async function saveTitleEdit(recordId) {
  const editor = titleEditor(recordId);
  if (!editor) return;
  const input = editor.querySelector(".record-title-input");
  const nextName = input.value.trim();
  if (!nextName) {
    input.focus();
    return;
  }
  const saveButton = editor.querySelector("[data-save-title]");
  saveButton.disabled = true;
  try {
    const response = await fetch(`/api/records/${encodeURIComponent(recordId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ book_name: nextName }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "保存失败");
    }
    state.records = payload.records || state.records.map((record) => (record.record_id === recordId ? payload.record : record));
    renderRecords(filteredRecords());
    elements.sourceHint.textContent = "书名已更新。";
  } catch (error) {
    elements.sourceHint.textContent = error.message;
    input.focus();
  } finally {
    saveButton.disabled = false;
  }
}

function finishTitleEdit(editor) {
  editor.classList.remove("is-editing");
  editor.querySelector(".record-title-text").hidden = false;
  editor.querySelector(".record-title-input").hidden = true;
  editor.querySelector("[data-edit-title]").hidden = false;
  editor.querySelector("[data-save-title]").hidden = true;
  editor.querySelector("[data-cancel-title]").hidden = true;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function readerStatusText(record) {
  if (record.refinement_status === "completed") {
    return `${record.refined_chapter_count || 0} 章`;
  }
  if (record.refinement_status === "partial") {
    return `部分 ${record.refined_chapter_count || 0} 章`;
  }
  if (record.refinement_status === "skipped") {
    return "未配置";
  }
  return "未运行";
}

function toggleTimelinePicker() {
  const shouldOpen = elements.timelinePopover.hidden;
  elements.timelinePopover.hidden = !shouldOpen;
  elements.timelineToggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
  if (shouldOpen) {
    loadTimelineSources().catch((error) => {
      elements.scriptHint.textContent = error.message;
    });
  }
}

function closeTimelinePicker() {
  if (!elements.timelinePopover) return;
  elements.timelinePopover.hidden = true;
  elements.timelineToggle.setAttribute("aria-expanded", "false");
}

function updateSelectedTimelineCount() {
  elements.selectedTimelineCount.textContent = String(state.selectedTimelineIds.size);
  elements.timelineToggle.classList.toggle("has-selection", state.selectedTimelineIds.size > 0);
}

function updateScriptHint() {
  const selectedSources = state.timelineSources.filter((source) => state.selectedTimelineIds.has(source.record_id));
  const startYear = parseYearInput(elements.scriptStartYearInput.value);
  const endYear = parseYearInput(elements.scriptEndYearInput.value);
  const rangeText = startYear.ok && endYear.ok ? `时间范围：${formatTimeRange(startYear.value, endYear.value)}。` : "";
  if (!selectedSources.length) {
    elements.scriptHint.textContent = `${rangeText}请选择要引用的书籍时间线。`;
    return;
  }
  elements.scriptHint.textContent = `${rangeText}已选择 ${selectedSources.length} 本书：${selectedSources
    .map((source) => source.book_name)
    .join("、")}`;
}

async function prepareScriptGeneration() {
  const topic = elements.scriptTopicInput.value.trim();
  const startYear = parseYearInput(elements.scriptStartYearInput.value);
  const endYear = parseYearInput(elements.scriptEndYearInput.value);
  if (!topic) {
    elements.scriptHint.textContent = "请先输入短剧主题。";
    elements.scriptTopicInput.focus();
    return;
  }
  if (!startYear.ok) {
    elements.scriptHint.textContent = startYear.message;
    elements.scriptStartYearInput.focus();
    return;
  }
  if (!endYear.ok) {
    elements.scriptHint.textContent = endYear.message;
    elements.scriptEndYearInput.focus();
    return;
  }
  if (!state.selectedTimelineIds.size) {
    elements.scriptHint.textContent = "请至少勾选一本书的时间线。";
    toggleTimelinePicker();
    return;
  }
  const timeRange = formatTimeRange(startYear.value, endYear.value);
  elements.scriptGenerateButton.disabled = true;
  elements.scriptGenerateButton.textContent = "生成中";
  elements.scriptHint.textContent = `正在检索 ${timeRange} 的时间线材料，并调用剧本 Agent...`;
  closeTimelinePicker();
  try {
    const response = await fetch("/api/script/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topic,
        time_range: timeRange,
        time_start_year: startYear.value,
        time_end_year: endYear.value,
        timeline_record_ids: [...state.selectedTimelineIds],
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "剧本生成失败");
    }
    renderScriptResult(payload.result, "script");
    await loadScriptGenerations();
    elements.scriptHint.textContent = `生成完成：匹配 ${payload.result.matched_event_count} 个时间线模块。`;
  } catch (error) {
    elements.scriptHint.textContent = error.message;
  } finally {
    elements.scriptGenerateButton.disabled = false;
    elements.scriptGenerateButton.textContent = "生成剧本";
  }
}

function parseYearInput(value) {
  const text = String(value || "").trim();
  if (!text) {
    return { ok: false, message: "请填写开始年份和结束年份。" };
  }
  if (!/^-?\d+$/.test(text)) {
    return { ok: false, message: "年份只能输入整数，可以用负数表示公元前/远古年份。" };
  }
  return { ok: true, value: Number.parseInt(text, 10) };
}

function formatTimeRange(startYear, endYear) {
  const start = Math.min(startYear, endYear);
  const end = Math.max(startYear, endYear);
  return `${formatYearLabel(start)} — ${formatYearLabel(end)}`;
}

function formatYearLabel(year) {
  if (year < 0) {
    const absoluteYear = Math.abs(year);
    if (absoluteYear >= 10000) {
      return `${Number(absoluteYear / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}万年前`;
    }
    return `公元前 ${absoluteYear.toLocaleString("zh-CN")} 年`;
  }
  if (year > 0) {
    return `公元 ${year.toLocaleString("zh-CN")} 年`;
  }
  return "公元 0 年";
}

function renderScriptResult(result, mode = "script") {
  elements.scriptResultPanel.hidden = false;
  elements.scriptResultTitle.textContent = result.script.title || result.topic;
  const article = result.script.article || "";
  if (article) {
    const paragraphs = article
      .split(/\n{2,}/)
      .map((paragraph) => paragraph.trim())
      .filter(Boolean)
      .map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`)
      .join("");
    elements.scriptScenes.innerHTML = `
      ${renderFactCards(result.script.fact_cards || [])}
      ${renderCausalChain(result.script.causal_chain || [])}
      ${renderScriptOutline(result.script.outline || [])}
      <article class="script-article-card"><h3>4. 完整剧本</h3>${paragraphs}</article>
      ${renderFactBoundaries(result.script.fact_boundaries || {})}
    `;
  } else {
    elements.scriptScenes.innerHTML = (result.script.scenes || [])
      .map((scene) => {
        const dialogue = (scene.dialogue || [])
          .map((line) => `<li><strong>${escapeHtml(line.speaker)}</strong>：${escapeHtml(line.line)}</li>`)
          .join("");
        return `
          <article class="script-scene-card">
            <div class="scene-kicker">场景 ${scene.scene} · ${escapeHtml(scene.setting || "")}</div>
            <h4>${escapeHtml(scene.title || "")}</h4>
            <p>${escapeHtml(scene.narration || "")}</p>
            ${dialogue ? `<ul>${dialogue}</ul>` : ""}
            ${scene.visual_notes ? `<div class="visual-note">${escapeHtml(scene.visual_notes)}</div>` : ""}
          </article>
        `;
      })
      .join("") || '<div class="empty-record">暂无剧本文稿</div>';
  }

  elements.scriptSubjects.innerHTML = (result.subjects || [])
    .map((subject) => {
      return `
        <article class="subject-card subject-row">
          <div class="subject-row-head">
            <strong>${escapeHtml(subject.name)}</strong>
            <span>${escapeHtml(subject.type)}</span>
          </div>
          <p>${escapeHtml(subject.intro)}</p>
          <em>${escapeHtml(subject.visual_modeling)}</em>
        </article>
      `;
    })
    .join("") || '<div class="empty-record">主体尚未生成，可在需要时单独生成</div>';

  elements.scriptMapShots.innerHTML = (result.map_shots || [])
    .map((shot) => {
      const places = (shot.places || []).join("、");
      const mapUrl = shot.map_render_url || "";
      return `
        <article class="map-shot-card">
          ${mapUrl ? `<img src="${escapeHtml(mapUrl)}" alt="${escapeHtml(shot.title || "地图画面")}" loading="lazy">` : ""}
          <div><strong>${escapeHtml(shot.title)}</strong><span>${escapeHtml(shot.region)}</span></div>
          <p>${escapeHtml(shot.description)}</p>
          <em>${escapeHtml(places)}</em>
        </article>
      `;
    })
    .join("") || '<div class="empty-record">地点画面尚未生成，可在需要时单独生成</div>';

  renderScriptReview(result);

  elements.scriptMatchedEvents.innerHTML = (result.matched_events || [])
    .map((event) => {
      return `
        <article class="matched-event-card">
          <div>${escapeHtml(event.book_name || "")} · ${escapeHtml(event.time_label || "")}</div>
          <h4>${escapeHtml(event.title || "")}</h4>
          <p>${escapeHtml(event.content || "")}</p>
        </article>
      `;
    })
    .join("") || '<div class="empty-record">暂无匹配材料</div>';
  setScriptViewerMode(mode);
  elements.scriptResultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderFactCards(cards) {
  if (!cards.length) return "";
  return `
    <section class="script-structure-card">
      <h3>1. 史实提取</h3>
      <div class="fact-card-grid">
        ${cards
          .map(
            (card) => `
              <article class="fact-card">
                <div><strong>${escapeHtml(card.id || "")}</strong><span>${escapeHtml(card.confidence || "")}</span></div>
                <h4>${escapeHtml(card.fact || "")}</h4>
                <p>${escapeHtml(card.time || "")} · ${escapeHtml(card.place || "")}</p>
                <em>${escapeHtml(card.source_basis || "")}</em>
                <p>${escapeHtml(card.drama_direction || "")}</p>
                ${card.do_not_overstate ? `<small>${escapeHtml(card.do_not_overstate)}</small>` : ""}
              </article>
            `
          )
          .join("")}
      </div>
    </section>
  `;
}

function renderCausalChain(items) {
  if (!items.length) return "";
  return `
    <section class="script-structure-card">
      <h3>2. 因果链</h3>
      <ol class="causal-chain-list">
        ${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ol>
    </section>
  `;
}

function renderScriptOutline(items) {
  if (!items.length) return "";
  return `
    <section class="script-structure-card">
      <h3>3. 场景大纲</h3>
      <div class="outline-list">
        ${items
          .map(
            (item) => `
              <article class="outline-card">
                <h4>${escapeHtml(item.title || "")}</h4>
                <p>${escapeHtml(item.core_point || "")}</p>
                <dl>
                  <dt>画面</dt><dd>${escapeHtml(item.opening_image || "")}</dd>
                  <dt>动作</dt><dd>${escapeHtml(item.human_action || "")}</dd>
                  <dt>问题</dt><dd>${escapeHtml(item.conflict || "")}</dd>
                  <dt>变化</dt><dd>${escapeHtml(item.change || "")}</dd>
                  <dt>代价</dt><dd>${escapeHtml(item.cost || "")}</dd>
                  <dt>衔接</dt><dd>${escapeHtml(item.transition || "")}</dd>
                </dl>
              </article>
            `
          )
          .join("")}
      </div>
    </section>
  `;
}

function renderFactBoundaries(boundaries) {
  const sections = [
    ["explicitly_supported", "原始材料明确支持"],
    ["dramatized_inference", "合理场景化改写"],
    ["needs_manual_check", "需要人工核对"],
    ["possible_overstatement", "可能过度夸张"],
    ["suggested_sources", "建议补充资料"],
  ];
  const hasAny = sections.some(([key]) => (boundaries[key] || []).length);
  if (!hasAny) return "";
  return `
    <section class="script-structure-card">
      <h3>5. 事实边界与人工核对点</h3>
      <div class="boundary-grid">
        ${sections
          .map(([key, title]) => {
            const values = boundaries[key] || [];
            return `
              <article>
                <strong>${title}</strong>
                <ul>${(values.length ? values : ["无"]).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
              </article>
            `;
          })
          .join("")}
      </div>
    </section>
  `;
}

function renderScriptReview(result) {
  const review = result.script_review || {};
  const history = result.review_history || [];
  if (!review.score && !review.verdict && !history.length) {
    elements.scriptReview.innerHTML = '<div class="empty-record">暂无审查报告</div>';
    return;
  }
  const issues = (review.issues || [])
    .map((issue) => {
      return `
        <li>
          <strong>${escapeHtml(issue.severity || "")} · ${escapeHtml(issue.category || "")}</strong>
          <span>${escapeHtml(issue.description || "")}</span>
          ${issue.suggestion ? `<em>${escapeHtml(issue.suggestion)}</em>` : ""}
        </li>
      `;
    })
    .join("");
  const missing = (review.missing_content || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  elements.scriptReview.innerHTML = `
    <article class="script-review-card ${review.passed ? "passed" : "needs-work"}">
      <div class="script-review-summary">
        <strong>${review.passed ? "审查通过" : "需要返修"}</strong>
        <span>${escapeHtml(String(review.score || "-"))}/5</span>
        <span>返修 ${escapeHtml(String(result.revision_count || 0))} 次</span>
      </div>
      ${review.verdict ? `<p>${escapeHtml(review.verdict)}</p>` : ""}
      <div class="script-review-grid">
        <div><strong>主题贴合</strong><p>${escapeHtml(review.theme_alignment || "")}</p></div>
        <div><strong>完整性</strong><p>${escapeHtml(review.story_completeness || "")}</p></div>
        <div><strong>连贯性</strong><p>${escapeHtml(review.continuity || "")}</p></div>
        <div><strong>材料利用</strong><p>${escapeHtml(review.material_usage || "")}</p></div>
        <div><strong>关键节点展开</strong><p>${escapeHtml(review.key_node_depth || "")}</p></div>
      </div>
      ${review.simplicity_risk ? `<div class="script-review-note"><strong>简陋风险</strong><p>${escapeHtml(review.simplicity_risk)}</p></div>` : ""}
      ${missing ? `<div class="script-review-list"><strong>缺失内容</strong><ul>${missing}</ul></div>` : ""}
      ${issues ? `<div class="script-review-list"><strong>审查问题</strong><ul>${issues}</ul></div>` : ""}
    </article>
  `;
}

async function openScriptGeneration(generationId, section) {
  const response = await fetch(`/api/script/generations/${encodeURIComponent(generationId)}`);
  const payload = await response.json();
  if (!response.ok) {
    elements.scriptHint.textContent = payload.error || "剧本记录读取失败";
    return;
  }
  renderScriptResult(payload.generation, section || "script");
}

async function deleteScriptGeneration(generationId) {
  const response = await fetch(`/api/script/generations/${encodeURIComponent(generationId)}`, { method: "DELETE" });
  const payload = await response.json();
  if (!response.ok) {
    elements.scriptHint.textContent = payload.error || "剧本记录删除失败";
    return;
  }
  state.scriptGenerations = payload.generations || [];
  renderScriptGenerations();
  elements.scriptHint.textContent = "剧本记录已删除。";
}

function focusScriptResultSection(section) {
  setScriptViewerMode(section || "script");
  elements.scriptResultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function setScriptViewerMode(mode) {
  const nextMode = ["script", "subjects", "maps"].includes(mode) ? mode : "script";
  elements.scriptViewerTabs.forEach((tab) => {
    const isActive = tab.dataset.scriptViewer === nextMode;
    tab.classList.toggle("active", isActive);
    tab.setAttribute("aria-selected", isActive ? "true" : "false");
  });
  elements.scriptViewerPanels.forEach((panel) => {
    const isActive = panel.dataset.scriptViewerPanel === nextMode;
    panel.hidden = !isActive;
    panel.classList.toggle("active", isActive);
  });
}

function outputPathToLink(path) {
  const marker = "/outputs/";
  const index = path.indexOf(marker);
  if (index >= 0) {
    return path.slice(index);
  }
  const outputsMarker = "drama-agent-system/outputs/";
  const outputsIndex = path.indexOf(outputsMarker);
  if (outputsIndex >= 0) {
    return `/outputs/${path.slice(outputsIndex + outputsMarker.length)}`;
  }
  return "#";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function cssEscape(value) {
  if (window.CSS && CSS.escape) {
    return CSS.escape(value);
  }
  return String(value).replaceAll('"', '\\"');
}

elements.uploadButton.addEventListener("click", () => elements.fileInput.click());
elements.fileInput.addEventListener("change", uploadSelectedFile);
elements.parseButton.addEventListener("click", parseSelectedSource);
elements.refreshButton.addEventListener("click", async () => {
  await loadSources();
  await loadRecords();
});

elements.commandInput.addEventListener("input", () => {
  elements.commandBox.classList.add("is-searching");
  renderRecords(filteredRecords());
});

["focus", "mouseenter"].forEach((eventName) => {
  elements.commandInput.addEventListener(eventName, () => {
    if (!state.parsing) {
      elements.commandBox.classList.add("is-searching");
    }
  });
});

elements.commandInput.addEventListener("mouseleave", () => {
  elements.commandBox.classList.remove("is-searching");
});

elements.navTabs.forEach((tab) => {
  tab.addEventListener("click", () => setActiveView(tab.dataset.viewTab));
});
elements.timelineToggle.addEventListener("click", toggleTimelinePicker);
elements.closeTimelinePicker.addEventListener("click", closeTimelinePicker);
elements.scriptGenerateButton.addEventListener("click", prepareScriptGeneration);
elements.refreshScriptRecordsButton.addEventListener("click", loadScriptGenerations);
elements.scriptViewerTabs.forEach((tab) => {
  tab.addEventListener("click", () => focusScriptResultSection(tab.dataset.scriptViewer));
});
elements.sceneModuleTabs.forEach((tab) => {
  tab.addEventListener("click", () => setSceneBuilderTab(tab.dataset.sceneTab));
});
elements.visualModeTabs.forEach((tab) => {
  tab.addEventListener("click", () => setVisualSubjectMode(tab.dataset.visualMode));
});
elements.visualScriptBackButtons.forEach((button) => {
  button.addEventListener("click", showVisualScriptList);
});
elements.visualUploadButton.addEventListener("click", () => elements.visualScriptFileInput.click());
elements.visualScriptFileInput.addEventListener("change", uploadVisualScript);
elements.visualScriptSelect.addEventListener("change", () => {
  selectVisualScript(elements.visualScriptSelect.value);
});
elements.visualSubjectSearchInput.addEventListener("input", () => {
  state.visualSubjectQuery = elements.visualSubjectSearchInput.value;
  renderVisualSubjectPool();
});
elements.visualExtractButton.addEventListener("click", extractVisualSubjectsFromScript);
elements.refreshVisualSubjectsButton.addEventListener("click", () => {
  loadVisualSubjects().catch((error) => setVisualStatus(error.message, "error"));
});
elements.refreshVisualScriptSubjectsButton.addEventListener("click", () => {
  loadScriptVisualSubjects(state.selectedVisualScriptId).catch((error) => setVisualStatus(error.message, "error"));
});
document.addEventListener("click", (event) => {
  if (!elements.timelinePicker.contains(event.target) && !elements.timelinePopover.hidden) {
    closeTimelinePicker();
  }
});
window.addEventListener("hashchange", applyHashView);
["input", "change"].forEach((eventName) => {
  elements.scriptTopicInput.addEventListener(eventName, updateScriptHint);
  elements.scriptStartYearInput.addEventListener(eventName, updateScriptHint);
  elements.scriptEndYearInput.addEventListener(eventName, updateScriptHint);
});

applyHashView();

Promise.all([loadSources(), loadRecords(), loadScriptGenerations()]).catch((error) => {
  setCommandState("error", error.message);
});
