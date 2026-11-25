/* Sigma Investments — progressive enhancements for the server-rendered UI.
   Every control is optional: forms still work without this file. */

(function () {
  "use strict";

  var $ = function (selector, scope) {
    return (scope || document).querySelector(selector);
  };
  var $$ = function (selector, scope) {
    return Array.prototype.slice.call((scope || document).querySelectorAll(selector));
  };

  var EYE = '<path d="M2.8 12S6.3 6 12 6s9.2 6 9.2 6-3.5 6-9.2 6-9.2-6-9.2-6Z" stroke="currentColor" stroke-width="1.7"/><circle cx="12" cy="12" r="2.6" stroke="currentColor" stroke-width="1.7"/>';
  var EYE_OFF = '<path d="m4 4 16 16M9.5 6.5A9.8 9.8 0 0 1 12 6c5.7 0 9.2 6 9.2 6a15 15 0 0 1-3.4 3.9M6.7 8.1A16 16 0 0 0 2.8 12S6.3 18 12 18c1 0 1.9-.2 2.7-.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>';

  function setSvgPaths(button, paths) {
    var svg = button.querySelector("svg");
    if (svg) {
      svg.innerHTML = paths;
    }
  }

  /* ------------------------------------------------------------- mobile nav */
  var menuToggle = $("[data-menu-toggle]");
  var mobileMenu = $("[data-mobile-menu]");

  function closeMobileMenu() {
    if (!mobileMenu || mobileMenu.hidden) return;
    mobileMenu.hidden = true;
    if (menuToggle) {
      menuToggle.setAttribute("aria-expanded", "false");
      menuToggle.querySelector(".sr-only").textContent = "Open menu";
    }
  }

  if (menuToggle && mobileMenu) {
    menuToggle.addEventListener("click", function () {
      var isOpen = !mobileMenu.hidden;
      mobileMenu.hidden = isOpen;
      menuToggle.setAttribute("aria-expanded", String(!isOpen));
      menuToggle.querySelector(".sr-only").textContent = isOpen
        ? "Open menu"
        : "Close menu";
    });
  }

  /* --------------------------------------------------------------- sidebar */
  var sidebar = $("[data-sidebar]");
  var openSidebar = $("[data-sidebar-open]");
  var closeSidebar = $("[data-sidebar-close]");
  var overlay = $("[data-sidebar-overlay]");

  function setSidebar(open) {
    if (!sidebar) return;
    sidebar.classList.toggle("is-open", open);
    document.body.classList.toggle("sidebar-locked", open);
    if (overlay) overlay.hidden = !open;
    if (openSidebar) openSidebar.setAttribute("aria-expanded", String(open));
    if (open) {
      var first = sidebar.querySelector("a, button");
      if (first) first.focus({ preventScroll: true });
    } else if (openSidebar && document.activeElement === closeSidebar) {
      openSidebar.focus({ preventScroll: true });
    }
  }

  if (openSidebar) {
    openSidebar.addEventListener("click", function () {
      setSidebar(true);
    });
  }
  if (closeSidebar) {
    closeSidebar.addEventListener("click", function () {
      setSidebar(false);
    });
  }
  if (overlay) {
    overlay.addEventListener("click", function () {
      setSidebar(false);
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    closeMobileMenu();
    if (sidebar && sidebar.classList.contains("is-open")) setSidebar(false);
  });

  window.addEventListener("resize", function () {
    if (window.innerWidth > 1024) {
      if (sidebar) setSidebar(false);
      closeMobileMenu();
    }
  });

  /* ---------------------------------------------------------------- toasts */
  $$("[data-dismiss-flash]").forEach(function (button) {
    button.addEventListener("click", function () {
      var toast = button.closest(".toast");
      if (!toast) return;
      toast.classList.add("is-leaving");
      window.setTimeout(function () {
        toast.remove();
      }, 240);
    });
  });

  $$(".toast").forEach(function (toast) {
    window.setTimeout(function () {
      if (!toast.isConnected) return;
      toast.classList.add("is-leaving");
      window.setTimeout(function () {
        toast.remove();
      }, 240);
    }, 7000);
  });

  /* -------------------------------------------------------- password field */
  $$("[data-password-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      var wrap = button.closest(".password-wrap");
      var input = wrap ? wrap.querySelector("input") : null;
      if (!input) return;
      var showing = input.type === "text";
      input.type = showing ? "password" : "text";
      button.setAttribute("aria-pressed", String(!showing));
      button.setAttribute("aria-label", showing ? "Show password" : "Hide password");
      setSvgPaths(button, showing ? EYE : EYE_OFF);
    });
  });

  function passwordScore(value) {
    if (!value) return 0;
    var score = 0;
    if (value.length >= 12) score += 1;
    if (value.length >= 16) score += 1;
    if (/[a-z]/.test(value) && /[A-Z]/.test(value)) score += 1;
    if (/\d/.test(value)) score += 1;
    if (/[^A-Za-z0-9]/.test(value)) score += 1;
    return Math.min(4, score);
  }

  $$("[data-password-meter]").forEach(function (meter) {
    var input = document.querySelector("[data-password-input]");
    if (!input) return;
    var update = function () {
      meter.setAttribute("data-score", String(passwordScore(input.value)));
    };
    input.addEventListener("input", update);
    update();
  });

  /* ------------------------------------------------------------- demo fill */
  $$("[data-demo-email]").forEach(function (button) {
    button.addEventListener("click", function () {
      var form = document.querySelector("form.auth-form");
      if (!form) return;
      var email = form.querySelector('input[name="email"]');
      var password = form.querySelector('input[name="password"]');
      if (!email || !password) return;
      email.value = button.getAttribute("data-demo-email") || "";
      password.value = button.getAttribute("data-demo-password") || "";
      email.dispatchEvent(new Event("input", { bubbles: true }));
      password.dispatchEvent(new Event("input", { bubbles: true }));
      var submit = form.querySelector('button[type="submit"]');
      if (submit) submit.focus({ preventScroll: true });
      button.classList.add("is-used");
    });
  });

  /* --------------------------------------------------------- signup toggle */
  var signupForm = $("[data-signup-form]");
  if (signupForm) {
    var referralInput = signupForm.querySelector('input[name="referral_code"]');
    var workspaceField = $("[data-workspace-field]");
    var workspaceInput = workspaceField
      ? workspaceField.querySelector("input")
      : null;
    var syncWorkspace = function () {
      if (!workspaceField) return;
      var invited = referralInput && referralInput.value.trim().length > 0;
      workspaceField.hidden = Boolean(invited);
      if (workspaceInput) {
        workspaceInput.required = !invited;
      }
    };
    if (referralInput) {
      referralInput.addEventListener("input", syncWorkspace);
      syncWorkspace();
    }
  }

  /* ---------------------------------------------------------- plan select */
  $$("[data-select-plan]").forEach(function (button) {
    button.addEventListener("click", function () {
      var code = button.getAttribute("data-select-plan");
      var form = document.querySelector(".ticket-form");
      if (!form) return;
      var select = form.querySelector('select[name="plan_code"]');
      if (select) {
        select.value = code;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      $$("[data-plan-card]").forEach(function (card) {
        card.classList.toggle(
          "is-selected",
          card.getAttribute("data-plan-card") === code
        );
      });
      form.scrollIntoView({ behavior: "smooth", block: "center" });
      var amount = form.querySelector('input[name="amount"]');
      if (amount) amount.focus({ preventScroll: true });
    });
  });

  /* ----------------------------------------------------------- amounts */
  var moneyFormat = function (cents) {
    return (Math.max(0, Number(cents) || 0) / 100).toFixed(2);
  };

  $$("[data-amount]").forEach(function (button) {
    button.addEventListener("click", function () {
      var target = button.closest("form");
      var input =
        button.getAttribute("data-amount-target") && target
          ? target.querySelector(
              '[data-amount-input="' +
                button.getAttribute("data-amount-target") +
                '"]'
            )
          : null;
      if (!input && target) input = target.querySelector('input[name="amount"]');
      if (!input) input = document.querySelector('input[name="amount"]');
      if (!input) return;
      input.value = button.getAttribute("data-amount") || "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus({ preventScroll: true });
    });
  });

  $$("[data-fill-max]").forEach(function (button) {
    button.addEventListener("click", function () {
      var form = button.closest("form");
      var input = form ? form.querySelector('input[name="amount"]') : null;
      if (!input) return;
      input.value = moneyFormat(button.getAttribute("data-fill-max"));
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus({ preventScroll: true });
    });
  });

  /* ------------------------------------------------------------- confirm */
  $$("[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm"))) {
        event.preventDefault();
      }
    });
  });

  /* --------------------------------------------------------------- copy */
  function copyText(value) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(value);
    }
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = value;
      area.setAttribute("readonly", "");
      area.className = "clipboard-helper";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
        resolve();
      } catch (error) {
        reject(error);
      } finally {
        area.remove();
      }
    });
  }

  $$("[data-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      var value = button.getAttribute("data-copy") || "";
      if (!value) return;
      copyText(value)
        .then(function () {
          button.classList.add("is-copied");
          var oldLabel = button.getAttribute("aria-label") || "Copy";
          button.setAttribute("aria-label", "Copied");
          window.setTimeout(function () {
            button.classList.remove("is-copied");
            button.setAttribute("aria-label", oldLabel);
          }, 1800);
        })
        .catch(function () {
          button.setAttribute("aria-label", "Copy failed");
        });
    });
  });

  /* --------------------------------------------------------- back button */
  $$("[data-history-back]").forEach(function (button) {
    button.addEventListener("click", function () {
      window.history.back();
    });
  });

  /* --------------------------------------------- prevent double submits */
  $$("form").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (event.defaultPrevented) return;
      if (!form.checkValidity()) return;
      var submit = form.querySelector('button[type="submit"]');
      if (!submit) return;
      submit.disabled = true;
      submit.classList.add("is-busy");
      window.setTimeout(function () {
        submit.disabled = false;
        submit.classList.remove("is-busy");
      }, 1200);
    });
  });

  /* -------------------------------------------------------- scroll header */
  var header = $("[data-header]");
  if (header) {
    var onScroll = function () {
      header.classList.toggle("is-scrolled", window.scrollY > 8);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }
})();
