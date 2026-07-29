
// Utility functions for the frontend-admin app

// Show a toast notification using iziToast library
function showToast(message, kind) {
  iziToast.show({
    title: kind.charAt(0).toUpperCase() + kind.slice(1),
    message: message,
    position: "topRight",
    color: kind === "success" ? "green" : kind === "error" ? "red" : "blue",
  });
}
