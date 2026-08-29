const reveals = document.querySelectorAll(".reveal");
const revealObserver = new IntersectionObserver(
  (entries, observer) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add("visible");
      observer.unobserve(entry.target);
    }
  },
  { threshold: 0.12 },
);

for (const element of reveals) revealObserver.observe(element);

const tagline = document.querySelector("[data-word-reveal]");
if (tagline) {
  const fragment = document.createDocumentFragment();
  let wordIndex = 0;
  for (const child of [...tagline.childNodes]) {
    if (child.nodeName === "BR") {
      fragment.append(document.createElement("br"));
      continue;
    }
    for (const token of (child.textContent || "").split(/(\s+)/)) {
      if (!token.trim()) {
        fragment.append(document.createTextNode(token));
        continue;
      }
      const word = document.createElement("span");
      word.className = "word";
      word.style.transitionDelay = `${wordIndex * 35}ms`;
      word.textContent = token;
      wordIndex += 1;
      fragment.append(word);
    }
  }
  tagline.replaceChildren(fragment);

  const wordObserver = new IntersectionObserver(
    (entries, observer) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        for (const word of entry.target.querySelectorAll(".word")) {
          word.classList.add("active");
        }
        observer.unobserve(entry.target);
      }
    },
    { rootMargin: "0px 0px -28% 0px", threshold: 0.35 },
  );
  wordObserver.observe(tagline);
}

for (const button of document.querySelectorAll("[data-copy]")) {
  button.addEventListener("click", async () => {
    const original = button.textContent;
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Copy failed";
    }
    window.setTimeout(() => {
      button.textContent = original;
    }, 1600);
  });
}
