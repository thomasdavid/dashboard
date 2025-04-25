document.addEventListener("DOMContentLoaded", function () {
  const embeds = document.querySelectorAll(".dashboard-embed");

  embeds.forEach(el => {
    const src = el.getAttribute("data-src");
    const iframe = document.createElement("iframe");
    iframe.src = src;
    iframe.style.width = "100%";
    iframe.style.height = "100vh";
    iframe.style.border = "none";
    iframe.title = "Dashboard embed";

    el.innerHTML = "";
    el.appendChild(iframe);
  });
});
