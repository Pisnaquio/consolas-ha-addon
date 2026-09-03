(() => {
  const MAX_FILE_BYTES = 8 * 1024 * 1024;
  const ALLOWED_MIME = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
  const ALLOWED_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".webp", ".gif"]);

  function validateImageFile(file, options = {}) {
    const maxBytes = options.maxBytes || MAX_FILE_BYTES;
    const name = String(file?.name || "");
    const extension = name.includes(".") ? `.${name.split(".").pop().toLowerCase()}` : "";
    if (!file) return { valid: false, message: "Seleccioná una imagen." };
    if (!ALLOWED_MIME.has(file.type) || !ALLOWED_EXTENSIONS.has(extension)) {
      return { valid: false, message: "La imagen debe ser JPG, PNG, WEBP o GIF." };
    }
    if (file.size > maxBytes) {
      return { valid: false, message: `La imagen supera el máximo de ${Math.round(maxBytes / 1024 / 1024)} MB.` };
    }
    return { valid: true };
  }

  function fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(new Error(`No se pudo leer ${file?.name || "la imagen"}.`));
      reader.readAsDataURL(file);
    });
  }

  async function uploadImageFile(file, options = {}) {
    const validation = validateImageFile(file, options);
    if (!validation.valid) throw new Error(validation.message);
    const dataUrl = await fileToDataUrl(file);
    const response = await fetch(`${window.CONSOLAS_API_BASE || "./api"}/media`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ dataUrl, fileName: file.name })
    });
    if (!response.ok) {
      let detail = "No se pudo guardar la imagen.";
      try { detail = (await response.json())?.error || detail; } catch { /* respuesta no JSON */ }
      throw new Error(detail);
    }
    return await response.json();
  }

  window.MediaUpload = { MAX_FILE_BYTES, validateImageFile, fileToDataUrl, uploadImageFile };
})();
