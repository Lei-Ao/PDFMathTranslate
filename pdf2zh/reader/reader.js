(() => {
  "use strict";
  const token = location.pathname.split("/").pop();
  const base = location.pathname.slice(0, location.pathname.lastIndexOf("/view/"));
  const $ = id => document.getElementById(id);
  const panels = {original: $("original"), translated: $("translated")};
  const pageInput = $("page"), zoomInput = $("zoom"), syncInput = $("sync");
  let metadata, activeSide = "original", frame = 0;
  const suppressed = new WeakMap(), observers = [], loading = new Set();
  const api = path => `${base}/${path}`;
  const padding = side => Number.parseFloat(getComputedStyle(panels[side]).paddingTop) || 0;
  const counts = side => metadata?.pages[side]?.length || 0;
  const pages = side => [...panels[side].querySelectorAll(".pdf-page")];
  const measure = document.createElement("canvas").getContext("2d");

  function position(side) {
    const panel = panels[side], list = pages(side);
    if (!list.length) return {index: 0, fraction: 0};
    let index = 0;
    for (let i = 1; i < list.length; i++) {
      if (list[i].offsetTop - padding(side) > panel.scrollTop + 2) break;
      index = i;
    }
    const page = list[index];
    return {index, fraction: Math.max(0, Math.min(1, (panel.scrollTop - page.offsetTop + padding(side)) / (page.offsetHeight + 16)))};
  }

  function setPosition(side, anchor) {
    const panel = panels[side], list = pages(side);
    if (!list.length) return;
    const page = list[Math.min(anchor.index, list.length - 1)];
    const top = Math.max(0, page.offsetTop - padding(side) + anchor.fraction * (page.offsetHeight + 16));
    suppressed.set(panel, performance.now() + 120);
    panel.scrollTop = top;
  }

  function updatePage(side) {
    const index = position(side).index;
    pageInput.value = index + 1;
    pageInput.max = counts(side);
    $("total").textContent = `/ ${counts(side)}`;
    $("previous").disabled = index === 0;
    $("next").disabled = index >= counts(side) - 1;
  }

  function goTo(value) {
    if (!metadata) return;
    const page = Math.max(1, Math.min(counts(activeSide), Math.trunc(Number(value)) || 1));
    for (const side of Object.keys(panels)) setPosition(side, {index: page - 1, fraction: 0});
    updatePage(activeSide);
  }

  function sizePages() {
    if (!metadata) return;
    const anchor = position(activeSide);
    for (const side of Object.keys(panels)) {
      const panel = panels[side], style = getComputedStyle(panel);
      const available = Math.max(100, panel.clientWidth - (Number.parseFloat(style.paddingLeft) || 0) - (Number.parseFloat(style.paddingRight) || 0));
      pages(side).forEach((element, index) => {
        const dimensions = metadata.pages[side][index];
        const width = zoomInput.value === "fit" ? available : dimensions.width * (96 / 72) * Number(zoomInput.value);
        element.style.width = `${width}px`;
        element.style.height = `${width * dimensions.height / dimensions.width}px`;
        element.dataset.scale = width / dimensions.width;
        layoutText(element);
      });
    }
    setPosition(activeSide, anchor);
    if (syncInput.checked) setPosition(activeSide === "original" ? "translated" : "original", anchor);
  }

  function layoutText(element) {
    const scale = Number(element.dataset.scale);
    for (const span of element.querySelectorAll(".text-layer span")) {
      const box = JSON.parse(span.dataset.box), fontSize = Number(span.dataset.size) * scale;
      span.style.left = `${box[0] * scale}px`;
      span.style.top = `${box[1] * scale}px`;
      span.style.fontSize = `${fontSize}px`;
      span.style.height = `${(box[3] - box[1]) * scale}px`;
      measure.font = `${fontSize}px sans-serif`;
      const textWidth = measure.measureText(span.textContent).width;
      span.style.transform = `scaleX(${textWidth ? (box[2] - box[0]) * scale / textWidth : 1})`;
    }
  }

  async function loadPage(element, side, index) {
    const key = `${side}/${index}`;
    if (element.classList.contains("loaded") || loading.has(key)) return;
    loading.add(key);
    element.querySelector(".page-error")?.remove();
    const image = new Image();
    image.alt = `${side === "original" ? "原文" : "译文"}第 ${index + 1} 页`;
    try {
      image.src = api(`page/${token}/${side}/${index + 1}.png`);
      await image.decode();
      element.prepend(image);
      element.classList.add("loaded");
      const response = await fetch(api(`text/${token}/${side}/${index + 1}`));
      if (response.ok) {
        const data = await response.json(), layer = document.createElement("div");
        layer.className = "text-layer";
        for (const item of data.spans) {
          const span = document.createElement("span");
          span.textContent = item.text;
          span.dataset.box = JSON.stringify(item.bbox);
          span.dataset.size = item.size;
          layer.append(span);
        }
        element.append(layer);
        layoutText(element);
      }
    } catch (error) {
      if (!element.classList.contains("loaded")) {
        const message = document.createElement("div"), retry = document.createElement("button");
        message.className = "page-error";
        message.append("页面加载失败"); retry.textContent = "重试";
        retry.onclick = () => loadPage(element, side, index);
        message.append(retry); element.append(message);
      }
    } finally { loading.delete(key); }
  }

  function createPages(side) {
    if (!counts(side)) return;
    const panel = panels[side];
    panel.replaceChildren();
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting) loadPage(entry.target, side, Number(entry.target.dataset.index));
    }, {root: panel, rootMargin: "400px 0px"});
    observers.push(observer);
    metadata.pages[side].forEach((dimensions, index) => {
      const page = document.createElement("div"), label = document.createElement("div");
      page.className = "pdf-page"; page.dataset.index = index;
      label.className = "page-number"; label.textContent = `正在加载第 ${index + 1} 页…`;
      page.append(label); panel.append(page); observer.observe(page);
    });
  }

  for (const [side, panel] of Object.entries(panels)) {
    panel.addEventListener("scroll", () => {
      if ((suppressed.get(panel) || 0) > performance.now()) return;
      activeSide = side;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        updatePage(side);
        if (syncInput.checked) {
          const other = side === "original" ? "translated" : "original";
          setPosition(other, position(side));
          const target = panels[other], maxLeft = panel.scrollWidth - panel.clientWidth;
          target.scrollLeft = maxLeft > 0 ? panel.scrollLeft / maxLeft * (target.scrollWidth - target.clientWidth) : 0;
        }
      });
    }, {passive: true});
    panel.addEventListener("wheel", () => suppressed.delete(panel), {passive: true});
    panel.addEventListener("pointerdown", () => { suppressed.delete(panel); activeSide = side; });
  }
  $("previous").onclick = () => goTo(Number(pageInput.value) - 1);
  $("next").onclick = () => goTo(Number(pageInput.value) + 1);
  pageInput.onchange = () => goTo(pageInput.value);
  pageInput.onkeydown = event => { if (event.key === "Enter") { goTo(pageInput.value); pageInput.blur(); } };
  zoomInput.onchange = sizePages;
  syncInput.onchange = () => { if (syncInput.checked) setPosition(activeSide === "original" ? "translated" : "original", position(activeSide)); };
  $("fullscreen").onclick = async () => {
    $("reader-menu").open = false;
    try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); }
    catch { $("notice").hidden = false; $("notice").textContent = "浏览器未允许全屏，请使用浏览器全屏模式。"; }
  };
  $("focus").onclick = () => { $("reader-menu").open = false; document.body.classList.add("focus-reading"); };
  // 在新窗口打开翻译工作台：本窗口继续留着当前这份文献。桌面版有 pywebview 桥，
  // 浏览器版退回 window.open。
  $("new-window").onclick = () => {
    $("reader-menu").open = false;
    const path = "/";
    const bridge = window.pywebview && window.pywebview.api;
    if (bridge && typeof bridge.open_window === "function") { bridge.open_window(path); return; }
    window.open(new URL(path, location.origin).href, "_blank", "noopener");
  };
  $("focus-exit").onclick = () => document.body.classList.remove("focus-reading");
  document.addEventListener("keydown", event => { if (event.key === "Escape") { document.body.classList.remove("focus-reading"); $("reader-menu").open = false; } });
  document.addEventListener("click", event => { if (!event.target.closest("#reader-menu")) $("reader-menu").open = false; });
  new ResizeObserver(sizePages).observe(document.querySelector("main"));

  async function start() {
    if (token === "empty") {
      for (const control of document.querySelectorAll("button,input,select")) control.disabled = true;
      $("new-window").disabled = false;  // 还没有文档时也能另开一个窗口去翻译
      return;
    }
    try {
      const response = await fetch(api(`document/${token}`));
      if (!response.ok) throw new Error("预览已失效，请刷新主页面或重新上传文件。");
      metadata = await response.json();
      $("filename").textContent = metadata.name;
      for (const [id, side] of [["download-mono", "translated"], ["download-dual", "dual"]]) {
        const link = $(id);
        if (metadata.downloads[side]) {
          link.hidden = false;
          link.href = api(`download/${token}/${side}`);
          link.setAttribute("download", "");
        }
      }
      for (const side of Object.keys(panels)) createPages(side);
      sizePages(); updatePage("original");
      if (counts("translated")) {
        $("status").textContent = `${counts("original")} 页原文 · ${counts("translated")} 页译文`;
        if (counts("original") !== counts("translated")) { $("notice").hidden = false; $("notice").textContent = "原文与译文页数不同，同步按页码匹配；请核对实际内容。"; }
      } else $("status").textContent = "原文已加载 · 等待翻译完成";
    } catch (error) { $("notice").hidden = false; $("notice").textContent = error.message; }
  }
  start();
})();
