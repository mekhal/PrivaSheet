/**
 * Bootstrap modal confirmDialog(message) -> Promise<boolean>.
 */
(function () {
  "use strict";

  let modalElement;
  let messageElement;
  let okButton;

  function ensureModal() {
    if (modalElement) {
      return;
    }

    modalElement = document.createElement("div");
    modalElement.className = "modal fade";
    modalElement.tabIndex = -1;
    modalElement.innerHTML = [
      '<div class="modal-dialog modal-dialog-centered">',
      '  <div class="modal-content">',
      '    <div class="modal-header">',
      '      <h1 class="modal-title fs-5">Confirm action</h1>',
      '      <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>',
      "    </div>",
      '    <div class="modal-body"><p class="mb-0" data-confirm-message></p></div>',
      '    <div class="modal-footer">',
      '      <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>',
      '      <button type="button" class="btn btn-danger" data-confirm-ok>Delete</button>',
      "    </div>",
      "  </div>",
      "</div>",
    ].join("");
    document.body.appendChild(modalElement);
    messageElement = modalElement.querySelector("[data-confirm-message]");
    okButton = modalElement.querySelector("[data-confirm-ok]");
  }

  window.confirmDialog = function confirmDialog(message) {
    ensureModal();
    messageElement.textContent = message;

    return new Promise((resolve) => {
      const modal = new bootstrap.Modal(modalElement);

      function cleanup(result) {
        okButton.removeEventListener("click", onOk);
        modalElement.removeEventListener("hidden.bs.modal", onHidden);
        resolve(result);
      }

      function onOk() {
        modal.hide();
        cleanup(true);
      }

      function onHidden() {
        cleanup(false);
      }

      okButton.addEventListener("click", onOk, { once: true });
      modalElement.addEventListener("hidden.bs.modal", onHidden, { once: true });
      modal.show();
    });
  };

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-delete-action]");
    if (!form || form.dataset.confirmed === "true") {
      return;
    }

    event.preventDefault();
    const message = form.dataset.confirmMessage || "Delete this item?";
    if (await window.confirmDialog(message)) {
      form.dataset.confirmed = "true";
      form.requestSubmit();
    }
  });
})();
