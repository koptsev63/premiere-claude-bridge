// Video effect names across Premiere UI languages.
//
// qe.project.getVideoEffectByName() takes the name shown in the Effects
// panel, i.e. the LOCALIZED one. With the Russian UI, "Black & White"
// returned an object, addVideoEffect() accepted it and nothing was added;
// "Черное и белое" worked (field case 20.10.2026). So a name is trusted only
// if it appears in qe.project.getVideoEffectList() of the running Premiere.
//
// The map below holds English names and their Russian twins as they appear in
// that list in Premiere Pro 26.0.2 (test/fixtures/video-effects-ru-26.0.2.txt).
// A mapped name is used only when it is present in the live list, and the
// add is still verified by counting the clip's components.

export const EN_TO_LOCAL = {
  ru: {
    "Black & White": "Черное и белое",
    "Lumetri Color": "Цвет Lumetri",
    "Crop": "Обрезать",
    "Horizontal Flip": "Зеркальное отражение по горизонтали",
    "Vertical Flip": "Зеркальное отражение по вертикали",
    "Warp Stabilizer": "Стабилизатор деформации",
    "Ultra Key": "Ключ Ultra",
    "Gaussian Blur (Legacy)": "Размытие по Гауссу (из прежней версии)",
    "Fast Blur": "Быстрое размытие",
    "Camera Blur": "Размытие для камеры",
    "Directional Blur (Legacy)": "Направленное размытие (предыдущая версия)",
    "Mosaic (Legacy)": "Мозаика (предыдущая версия)",
    "Magnify (Legacy)": "Увеличение (предыдущая версия)",
    "Linear Wipe (Legacy)": "Линейное вытеснение (предыдущая версия)",
    "Unsharp Mask": "Контурная резкость",
    "Sharpen": "Увеличить четкость",
    "Tint": "Оттенок",
    "Color Replace": "Замена цвета",
    "Gamma Correction": "Гамма-коррекция",
    "Extract": "Извлечь",
    "Levels": "Уровни",
    "Lens Distortion": "Искажение линзы",
    "Video Limiter": "Ограничитель видео",
    "Timecode": "Тайм-код",
    "Clip Name": "Имя клипа",
    "Luma Key": "Ключ яркости",
    "Color Key": "Цветовой ключ",
    "Track Matte Key": "Ключ маски дорожки",
    "Spherize": "Сферизация",
    "Invert": "Обратить",
    "Posterize": "Постеризация",
    "Posterize Time": "Время постеризации",
    "Brush Strokes": "Мазки кистью",
    "Gradient Wipe": "Градиентное вытеснение",
    "Roughen Edges": "Шероховатые края",
    "Corner Pin": "Привязка по углам",
    "Rolling Shutter Repair": "Устранение эффекта плавающего затвора",
    "Replicate": "Репликация",
    "Alpha Adjust": "Изменение альфа-канала",
    "Alpha Glow": "Свечение альфа-канала",
    "4-Color Gradient": "4-цветный градиент",
    "Offset": "Смещение",
    "Twirl": "Скручивание",
    "Lighting Effects": "Эффекты освещения",
    "Wave Warp": "Волнообразная деформация",
    "Echo": "Эхо",
    "Reduce Interlace Flicker": "Уменьшить мерцание в чересстрочной развертке",
    "Cineon Converter": "Конвертер Cineon",
    "Lightning": "Молния",
    "Drop Shadow": "Тень",
    "Basic 3D": "Базовый 3D",
    "Strobe Light": "Проблесковый свет",
    "Mirror": "Зеркало",
    "Lens Flare": "Блики",
    "Find Edges": "Выделение краев",
    "Color Emboss": "Цветовое тиснение",
    "Block Dissolve": "Растворение блоков",
    "Turbulent Displace": "Вихревое смещение",
    "Color Space Transform": "Преобразование цветового пространства",
    "Simple Text": "Простой текст",
    "Metadata & Timecode Burn-in": "Метаданные и вставка временного кода",
    "SDR Conform": "Согласование SDR",
    "Vector Motion": "Векторное движение",
    "Pixel Motion Blur": "Размытие пикселей в движении",
    "Opacity": "Непрозрачность",
    "Motion": "Движение",
  },
};

// Case, ё/е and spacing do not matter.
export function normalizeName(name) {
  return String(name || "")
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/\s+/g, " ")
    .trim();
}

// Looser key for fuzzy matching: "&" = "and" = "и", punctuation dropped.
function looseKey(name) {
  return normalizeName(name)
    .replace(/&/g, " and ")
    .replace(/\bи\b/g, " and ")
    .replace(/[^0-9a-zа-я]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function words(name) {
  return looseKey(name).split(" ").filter((w) => w.length > 1);
}

// Resolve what the caller asked for to a name present in `list`.
// Returns {name, how} or {error, candidates}. Never guesses between two.
export function resolveEffect(requested, list) {
  const names = Array.from(new Set((list || []).map(String)));
  const want = String(requested || "").trim();
  if (!want) return { error: "Empty effect name", candidates: [] };

  if (names.includes(want)) return { name: want, how: "exact" };

  const norm = normalizeName(want);
  const byNorm = names.filter((n) => normalizeName(n) === norm);
  if (byNorm.length === 1) return { name: byNorm[0], how: "case" };

  for (const [locale, table] of Object.entries(EN_TO_LOCAL)) {
    for (const [en, local] of Object.entries(table)) {
      if (normalizeName(en) === norm && names.includes(local)) {
        return { name: local, how: `map:${locale}` };
      }
    }
  }

  const key = looseKey(want);
  const byLoose = names.filter((n) => looseKey(n) === key);
  if (byLoose.length === 1) return { name: byLoose[0], how: "fuzzy" };

  // No safe pick: rank by shared words so the caller can choose.
  const ww = words(want);
  const scored = names
    .map((n) => {
      const nw = words(n);
      const shared = ww.filter((w) => nw.some((x) => x.startsWith(w) || w.startsWith(x))).length;
      return { n, shared };
    })
    .filter((s) => s.shared > 0)
    .sort((a, b) => b.shared - a.shared || a.n.length - b.n.length)
    .slice(0, 8)
    .map((s) => s.n);
  return {
    error: byLoose.length > 1
      ? `"${want}" matches several effects; pass the exact name`
      : `No video effect named "${want}" in this Premiere (UI language may differ). Use pr_list_video_effects to see the names.`,
    candidates: byLoose.length > 1 ? byLoose : scored,
  };
}
