// SolutionsHub progressive enhancement. No inline scripts (CSP), no eval.
(function () {
  "use strict";

  // Repeating contact rows on the intake form
  var addBtn = document.getElementById("add-contact");
  var tpl = document.getElementById("contact-row-template");
  var contacts = document.getElementById("contacts");
  if (addBtn && tpl && contacts) {
    addBtn.addEventListener("click", function () {
      contacts.appendChild(tpl.content.cloneNode(true));
    });
    contacts.addEventListener("click", function (e) {
      var btn = e.target.closest(".js-remove-row");
      if (!btn) return;
      var rows = contacts.querySelectorAll(".contact-row");
      if (rows.length > 1) btn.closest(".contact-row").remove();
      else btn.closest(".contact-row").querySelectorAll("input").forEach(function (i) { i.value = ""; });
    });
  }

  // Capability selection: at most N, "Other" requires text
  var caps = document.getElementById("capabilities");
  if (caps) {
    var max = parseInt(caps.getAttribute("data-max") || "3", 10);
    var count = document.getElementById("cap-count");
    var other = document.getElementById("other_text");
    function refresh() {
      var checked = caps.querySelectorAll('input[type=checkbox]:checked');
      caps.classList.toggle("maxed", checked.length >= max);
      caps.querySelectorAll('input[type=checkbox]').forEach(function (cb) {
        cb.disabled = !cb.checked && checked.length >= max;
      });
      if (count) count.textContent = checked.length + " of " + max + " selected";
      var otherBox = caps.querySelector('input[data-other]');
      if (other && otherBox) { other.required = otherBox.checked; other.style.display = otherBox.checked ? "" : "none"; }
    }
    caps.addEventListener("change", refresh);
    refresh();
  }

  // Confirmations on destructive forms
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var btn = form.querySelector("button.js-confirm[data-confirm]") || (form.classList.contains("js-confirm") ? form : null);
    var msg = btn ? (btn.getAttribute("data-confirm") || form.getAttribute("data-confirm")) : null;
    if (msg && !window.confirm(msg)) e.preventDefault();
  }, true);

  // Avoid double submits
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (e.defaultPrevented) return;
    window.setTimeout(function () {
      form.querySelectorAll("button[type=submit]").forEach(function (b) { b.disabled = true; });
    }, 0);
  });
})();
