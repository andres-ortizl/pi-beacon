const concepts = {
  stroke: {
    number: "01",
    title: "Single Stroke Frame",
    description:
      "One precise SVG line draws a technical frame, resolves a locator, then stays still.",
    note: "Press Replay to redraw the single perimeter line.",
  },
  gates: {
    number: "02",
    title: "Five Gate Reveal",
    description:
      "Architectural shutters part with an uneven rhythm and leave the real dashboard still.",
    note: "Press Replay to open the five gates.",
  },
  reading: {
    number: "03",
    title: "Reading Light",
    description:
      "A quiet benefit statement gains contrast one word at a time while the product stays still.",
    note: "Press Replay to relight the sentence in reading order.",
  },
  plinth: {
    number: "04",
    title: "Obsidian Plinth",
    description:
      "The proof visual responds as one weighted object with restrained pointer driven depth.",
    note: "Move the pointer around the dashboard. Replay returns it to rest.",
  },
  tidal: {
    number: "05",
    title: "Tidal Thread",
    description:
      "A single measured line travels through the page margin and connects three landmarks.",
    note: "Drag Trace position to control the line directly. Replay draws it once.",
  },
  eclipse: {
    number: "06",
    title: "Eclipse Portal",
    description:
      "A circular aperture expands once from focused detail to the complete dashboard.",
    note: "Press Replay to open the aperture.",
  },
  fold: {
    number: "07",
    title: "Folded Interface",
    description:
      "Three precise image plates settle into one flat and readable product view.",
    note: "Press Replay to unfold the interface.",
  },
  dither: {
    number: "08",
    title: "Dither Dawn",
    description:
      "Four coarse monochrome mask states resolve like a calm electronic ink display.",
    note: "Press Replay to resolve the dither mask.",
  },
  brush: {
    number: "09",
    title: "Brush Trace",
    description:
      "A visitor can leave one restrained vector stroke without changing product content.",
    note: "Draw over the dashboard with a mouse or pen. Replay clears the trace.",
  },
  polar: {
    number: "10",
    title: "Polar Ledger",
    description:
      "A sparse instrument moves through three discrete positions as an abstract page index.",
    note: "Press Replay to advance the index through its three states.",
  },
};

const page = document.querySelector(".motion-lab-page");
const title = document.querySelector("[data-concept-title]");
const number = document.querySelector("[data-concept-number]");
const description = document.querySelector("[data-concept-description]");
const interactionNote = document.querySelector("[data-interaction-note]");
const replayButton = document.querySelector("[data-replay]");
const reducedToggle = document.querySelector("[data-reduced-motion]");
const conceptButtons = [...document.querySelectorAll("[data-concept]")];
const prototypes = [...document.querySelectorAll("[data-prototype]")];
let currentConcept = "gates";
let tidalFrame = 0;
let plinthFrame = 0;

function activePrototype() {
  return prototypes.find(
    (prototype) => prototype.dataset.prototype === currentConcept,
  );
}

function setTidalProgress(value) {
  const prototype = prototypes.find(
    (item) => item.dataset.prototype === "tidal",
  );
  const range = prototype.querySelector("[data-tidal-range]");
  const normalized = Math.max(0, Math.min(1, Number(value) / 100));
  prototype.style.setProperty("--tidal-progress", normalized);
  range.value = String(Math.round(normalized * 100));
}

function animateTidal() {
  window.cancelAnimationFrame(tidalFrame);
  if (page.classList.contains("reduced-motion")) {
    setTidalProgress(100);
    return;
  }
  const started = performance.now();
  const duration = 900;
  const frame = (now) => {
    const progress = Math.min(1, (now - started) / duration);
    const eased = 1 - (1 - progress) ** 3;
    setTidalProgress(eased * 100);
    if (progress < 1) tidalFrame = window.requestAnimationFrame(frame);
  };
  tidalFrame = window.requestAnimationFrame(frame);
}

function clearBrush() {
  const polyline = document.querySelector("[data-brush] polyline");
  polyline.setAttribute("points", "");
}

function resetPlinth() {
  const plinth = document.querySelector("[data-plinth]");
  plinth.style.setProperty("--plinth-x", "0deg");
  plinth.style.setProperty("--plinth-y", "0deg");
  plinth.style.setProperty("--marker-x", "0px");
  plinth.style.setProperty("--marker-y", "0px");
}

function replay() {
  const prototype = activePrototype();
  prototype.classList.remove("running");
  void prototype.offsetWidth;
  prototype.classList.add("running");
  if (currentConcept === "tidal") {
    setTidalProgress(0);
    animateTidal();
  }
  if (currentConcept === "brush") clearBrush();
  if (currentConcept === "plinth") resetPlinth();
}

function selectConcept(concept) {
  if (!concepts[concept]) return;
  currentConcept = concept;
  const metadata = concepts[concept];
  title.textContent = metadata.title;
  number.textContent = metadata.number;
  description.textContent = metadata.description;
  interactionNote.textContent = metadata.note;
  for (const button of conceptButtons) {
    const selected = button.dataset.concept === concept;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  for (const prototype of prototypes) {
    const selected = prototype.dataset.prototype === concept;
    prototype.hidden = !selected;
    prototype.classList.toggle("active", selected);
  }
  const selectedButton = conceptButtons.find(
    (button) => button.dataset.concept === concept,
  );
  selectedButton.scrollIntoView({ block: "nearest", inline: "center" });
  history.replaceState(null, "", `#${concept}`);
  replay();
}

for (const button of conceptButtons) {
  button.addEventListener("click", () => selectConcept(button.dataset.concept));
}

replayButton.addEventListener("click", replay);
reducedToggle.addEventListener("change", () => {
  page.classList.toggle("reduced-motion", reducedToggle.checked);
  replay();
});

const tidalRange = document.querySelector("[data-tidal-range]");
tidalRange.addEventListener("input", () => {
  window.cancelAnimationFrame(tidalFrame);
  setTidalProgress(tidalRange.value);
});

const plinth = document.querySelector("[data-plinth]");
plinth.addEventListener("pointermove", (event) => {
  if (
    page.classList.contains("reduced-motion") ||
    event.pointerType === "touch"
  )
    return;
  window.cancelAnimationFrame(plinthFrame);
  plinthFrame = window.requestAnimationFrame(() => {
    const bounds = plinth.getBoundingClientRect();
    const horizontal = (event.clientX - bounds.left) / bounds.width - 0.5;
    const vertical = (event.clientY - bounds.top) / bounds.height - 0.5;
    plinth.style.setProperty("--plinth-x", `${vertical * -4}deg`);
    plinth.style.setProperty("--plinth-y", `${horizontal * 4}deg`);
    plinth.style.setProperty("--marker-x", `${horizontal * -8}px`);
    plinth.style.setProperty("--marker-y", `${vertical * -8}px`);
  });
});
plinth.addEventListener("pointerleave", resetPlinth);

const brush = document.querySelector("[data-brush]");
const brushPolyline = brush.querySelector("polyline");
let drawing = false;
let brushPoints = [];

function brushPoint(event) {
  const bounds = brush.getBoundingClientRect();
  return [
    ((event.clientX - bounds.left) / bounds.width) * 470,
    ((event.clientY - bounds.top) / bounds.height) * 533,
  ];
}

function renderBrush() {
  brushPolyline.setAttribute(
    "points",
    brushPoints.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" "),
  );
}

brush.addEventListener("pointerdown", (event) => {
  if (event.pointerType === "touch") return;
  drawing = true;
  brushPoints = [brushPoint(event)];
  brush.setPointerCapture(event.pointerId);
  renderBrush();
});
brush.addEventListener("pointermove", (event) => {
  if (!drawing) return;
  brushPoints.push(brushPoint(event));
  if (brushPoints.length > 96) brushPoints.shift();
  renderBrush();
});
brush.addEventListener("pointerup", () => {
  drawing = false;
});
brush.addEventListener("pointercancel", () => {
  drawing = false;
});

const initialConcept = location.hash.slice(1);
selectConcept(concepts[initialConcept] ? initialConcept : "gates");
