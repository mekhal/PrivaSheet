(function () {
  "use strict";

  const root = document.documentElement;
  const button = document.getElementById("themeToggle");
  const savedTheme = localStorage.getItem("privasheet.theme");

  function setTheme(theme) {
    root.dataset.bsTheme = theme;
    localStorage.setItem("privasheet.theme", theme);
    if (button) {
      button.textContent = theme === "dark" ? "Light mode" : "Dark mode";
    }
  }

  if (savedTheme === "dark" || savedTheme === "light") {
    setTheme(savedTheme);
  }

  if (button) {
    button.addEventListener("click", () => {
      setTheme(root.dataset.bsTheme === "dark" ? "light" : "dark");
    });
  }
})();
