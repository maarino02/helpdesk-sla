// Building page elements without ever parsing HTML.
//
// Text goes in with textContent and strings appended as children become text
// nodes, so a ticket titled "<img src=x onerror=...>" shows those characters
// instead of running them.

// el("a", { href: "/x/", text: "X", className: "link" }, ...children)
export function el(tag, options = {}, ...children) {
  const node = document.createElement(tag);
  const { className, text, ...attributes } = options;
  if (className) {
    node.className = className;
  }
  if (text !== undefined && text !== null) {
    node.textContent = text;
  }
  for (const [name, value] of Object.entries(attributes)) {
    node.setAttribute(name, value);
  }
  node.append(...children.filter((child) => child !== null && child !== undefined));
  return node;
}

// Adds one <option> per entry of { value: label } after the select's own options.
export function addOptions(select, labels) {
  for (const [value, label] of Object.entries(labels)) {
    select.append(el("option", { value, text: label }));
  }
}

// A coloured label for a code: badge("status", "open", STATUS).
export function badge(kind, value, labels) {
  return el("span", { className: `badge ${kind}-${value}`, text: labels[value] ?? value });
}
