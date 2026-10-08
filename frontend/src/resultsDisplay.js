const LIST_MARKER = /^\s{0,3}(?:[-*+]\s+|(?:\d+|[٠-٩۰-۹]+)[.)]\s+)/u;
const RESULT_START = /^\s{0,3}(?:[-*+]\s+|(?:\d+|[٠-٩۰-۹]+)[.)]\s+|\||\*\*[^*]{1,80}[:：]\*\*\s*\S|[^:：\n]{1,60}[:：]\s+[\p{N}])/u;

const SPORT_ICONS = [
  [/aikido|karate|judo|taekwondo|martial|أيكيدو|كاراتيه|جودو|تايكوندو/iu, "🥋"],
  [/football|soccer|كرة القدم/iu, "⚽"],
  [/basketball|كرة السلة/iu, "🏀"],
  [/netball|volleyball|كرة الطائرة|نتبول/iu, "🏐"],
  [/rugby|رغبي/iu, "🏉"],
  [/tennis|squash|تنس|اسكواش/iu, "🎾"],
  [/chess|شطرنج/iu, "♟️"],
  [/skating|التزلج/iu, "⛸️"],
  [/billiard|snooker|بلياردو/iu, "🎱"],
  [/paddle|surf|ركوب الأمواج/iu, "🏄"],
  [/paraglid|aeronautic|مظلي|طيران/iu, "🪂"],
  [/wrestl|مصارعة/iu, "🤼"],
  [/swimm|سباحة/iu, "🏊"],
  [/athletic|running|جري|ألعاب القوى/iu, "🏃"],
  [/boxing|ملاكمة/iu, "🥊"],
  [/cycling|دراجات/iu, "🚴"],
  [/badminton|ريشة/iu, "🏸"],
  [/handball|كرة اليد/iu, "🤾"],
  [/cricket|كريكيت/iu, "🏏"],
  [/baseball|بيسبول/iu, "⚾"],
  [/rowing|تجديف/iu, "🚣"],
  [/sail|إبحار/iu, "⛵"],
  [/horse|equestrian|فروسية/iu, "🏇"],
  [/gymnast|جمباز/iu, "🤸"],
];

const LABEL_HEADER = /sport|discipline|athlete|participant|name|region|city|club|country|nationality|gender|category|الرياضة|رياضي|لاعب|اسم|منطقة|مدينة|نادي|جنسية|جنس|فئة/iu;
const METRIC_HEADER = /rank|position|count|total|registration|score|percent|number|amount|federation|athlete|ترتيب|مركز|عدد|إجمالي|تسجيل|نتيجة|اتحاد|رياضي/iu;

function cleanMarkdown(value) {
  return value
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`~]/g, "")
    .replace(/\\([\\`*_{}\[\]()#+\-.!])/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

export function splitResultsMarkdown(markdown) {
  const lines = markdown.split(/\r?\n/);
  const resultsStartIndex = lines.findIndex((line) => RESULT_START.test(line));

  if (resultsStartIndex === -1) return null;

  return {
    summary: lines.slice(0, resultsStartIndex).join("\n").trim(),
    results: lines.slice(resultsStartIndex).join("\n").trim(),
  };
}

function resultIcon(label, context) {
  for (const [pattern, icon] of SPORT_ICONS) {
    if (pattern.test(label)) return icon;
  }
  if (/top|rank|leaderboard|ترتيب|الأعلى|أفضل/iu.test(context)) return "🏆";
  if (/athlete|participant|لاعب|رياضي/iu.test(context)) return "👥";
  if (/federation|اتحاد/iu.test(context)) return "🏛️";
  if (/region|city|location|منطقة|مدينة|موقع/iu.test(context)) return "📍";
  if (/registration|count|total|تسجيل|عدد|إجمالي/iu.test(context)) return "📈";
  if (/club|arena|facility|نادي|ملعب|منشأة/iu.test(context)) return "🏟️";
  return "📊";
}

function makeCard(label, value, valueLabel, details, context) {
  return {
    icon: resultIcon(label, context),
    label: label || "Not specified",
    value,
    valueLabel,
    details: details.filter(Boolean),
  };
}

function splitEntry(text) {
  const entry = cleanMarkdown(text).replace(/^\p{Extended_Pictographic}\s*/u, "");
  const separator = entry.match(/\s+(?:·|—|–|-)\s+|:\s+|：\s+/u);
  if (!separator || separator.index === undefined) {
    return { label: entry, value: "", valueLabel: "" };
  }

  const label = entry.slice(0, separator.index).trim();
  const value = entry.slice(separator.index + separator[0].length).trim();
  return label && value
    ? { label, value, valueLabel: "" }
    : { label: entry, value: "", valueLabel: "" };
}

function splitTableRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(cleanMarkdown);
}

function parseTable(markdown, context) {
  const lines = markdown.split(/\r?\n/);
  const tableStart = lines.findIndex((line) => /^\s*\|/.test(line));
  if (tableStart === -1) return null;

  const tableLines = [];
  for (const line of lines.slice(tableStart)) {
    if (!/^\s*\|/.test(line)) break;
    tableLines.push(line);
  }
  const rows = tableLines
    .filter((line) => !/^\s*\|?\s*:?-{3,}/.test(line))
    .map(splitTableRow)
    .filter((cells) => cells.some(Boolean));
  if (rows.length < 2) return null;

  const [headers, ...dataRows] = rows;
  const labelIndex = headers.findIndex((header) => LABEL_HEADER.test(header));
  const resolvedLabelIndex = labelIndex === -1 ? 0 : labelIndex;

  return dataRows.map((cells) => {
    const values = headers.map((header, index) => ({
      header,
      value: cells[index] || "",
      index,
    })).filter(({ value, index }) => index !== resolvedLabelIndex && value);
    const metric = values.find(({ header }) => METRIC_HEADER.test(header))
      || values.find(({ value }) => /^[\d٠-٩۰-۹,٬.%+-]+$/u.test(value))
      || values[0];
    const details = values
      .filter(({ index }) => !metric || index !== metric.index)
      .map(({ header, value }) => `${header}: ${value}`);

    return makeCard(
      cells[resolvedLabelIndex] || "Not specified",
      metric?.value || "",
      metric?.header || "",
      details,
      `${context} ${headers.join(" ")}`,
    );
  });
}

export function parseResultCards(markdown, context = "") {
  const tableCards = parseTable(markdown, context);
  if (tableCards?.length) return tableCards;

  const lines = markdown.split(/\r?\n/);
  const listEntries = lines.filter((line) => LIST_MARKER.test(line));
  if (listEntries.length) {
    return listEntries.map((line) => {
      const item = splitEntry(line.replace(LIST_MARKER, ""));
      return makeCard(item.label, item.value, item.valueLabel, [], context);
    }).filter((card) => card.label);
  }

  const keyValueEntries = lines
    .map((line) => cleanMarkdown(line))
    .map((line) => line.match(/^(.{1,80}?)\s*[:：]\s*(\S.*)$/u))
    .filter(Boolean);
  if (keyValueEntries.length) {
    return keyValueEntries.map(([, label, value]) => makeCard(label, value, "", [], context));
  }

  return null;
}