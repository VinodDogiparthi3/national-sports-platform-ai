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

const LABEL_HEADER = /sport|discipline|athlete|participant|age.?group|name|region|city|club|country|nationality|gender|category|الرياضة|رياضي|لاعب|اسم|فئة عمرية|منطقة|مدينة|نادي|جنسية|جنس|فئة/iu;
const METRIC_HEADER = /rank|position|count|total|registration|score|percent|number|amount|federation|athlete|ترتيب|مركز|عدد|إجمالي|تسجيل|نتيجة|اتحاد|رياضي/iu;
const DIMENSION_HEADER = /(?:^|[\s_-])(?:id|identifier|key|code|year|age|region|gender|nationality|name|category|type|status|date|month|quarter|group|district|city|country|description|comment|note)(?:$|[\s_-])|(?:id|identifier|key|code)$/iu;
const IDENTIFIER_HEADER = /(?:^|[\s_-])(?:id|identifier|key|code)(?:$|[\s_-])|(?:id|identifier|key|code)$/iu;
const METRIC_SUFFIX = /^(?:final|total|count|number|amount|value|sum|rate|score|metric)$/iu;

function isNumericValue(value) {
  return /^[+-]?(?:\d{1,3}(?:[,٬]\d{3})+|\d+)(?:[.٫]\d+)?%?$/u.test(value.trim())
    || /^[+-]?[٠-٩۰-۹]+(?:[٫٬][٠-٩۰-۹]+)*%?$/u.test(value.trim());
}

function metricCardLabel(header, context) {
  const words = cleanMarkdown(header).replace(/_/g, " ").split(/\s+/u).filter(Boolean);
  const year = words.find((word) => /^(?:19|20)\d{2}$/u.test(word));
  const category = words.filter((word) => !/^(?:19|20)\d{2}$/u.test(word) && !METRIC_SUFFIX.test(word));
  const contextTerms = cleanMarkdown(context)
    .replace(/[_-]+/g, " ")
    .split(/\s+/u)
    .filter((word) => word && !/^(?:view|views|statistics|statistic|estimate|estimates|data|result|results|by|the|for)$/iu.test(word))
    .filter((word) => !category.some((item) => item.toLocaleLowerCase() === word.toLocaleLowerCase()));
  const titleCase = (word) => /^\p{Lu}/u.test(word) ? word : word.charAt(0).toLocaleUpperCase() + word.slice(1);
  return [
    ...category.map(titleCase),
    ...contextTerms.map(titleCase),
    ...(year ? [`(${year})`] : []),
  ].join(" ");
}

function cleanMarkdown(value) {
  return value
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`~]/g, "")
    .replace(/\\([\\`*_{}[\]()#+\-.!])/g, "$1")
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
  const labelIndex = headers.findIndex((header) => LABEL_HEADER.test(header) && !IDENTIFIER_HEADER.test(header));
  const resolvedLabelIndex = labelIndex === -1 ? 0 : labelIndex;
  if (dataRows.some((cells) => !headers.some((header, index) => (
    index !== resolvedLabelIndex && !DIMENSION_HEADER.test(header) && isNumericValue(cells[index] || "")
  )))) return null;

  return dataRows.flatMap((cells) => {
    const values = headers.map((header, index) => ({
      header,
      value: cells[index] || "",
      index,
    })).filter(({ value, index }) => index !== resolvedLabelIndex && value);
    const metricColumns = values.filter(({ header, value }) => (
      !DIMENSION_HEADER.test(header) && isNumericValue(value)
    ));
    if (metricColumns.length > 1) {
      const metricIndexes = new Set(metricColumns.map(({ index }) => index));
      const sharedDetails = [
        ...(cells[resolvedLabelIndex] ? [`${headers[resolvedLabelIndex]}: ${cells[resolvedLabelIndex]}`] : []),
        ...values
        .filter(({ index }) => !metricIndexes.has(index))
        .map(({ header, value }) => `${header}: ${value}`),
      ];
      return metricColumns.map(({ header, value }) => makeCard(
        metricCardLabel(header, context),
        value,
        "",
        sharedDetails,
        `${context} ${headers.join(" ")}`,
      ));
    }
    const metric = metricColumns.find(({ header }) => METRIC_HEADER.test(header)) || metricColumns[0];
    const details = values
      .filter(({ index }) => !metric || index !== metric.index)
      .map(({ header, value }) => `${header}: ${value}`);

    return [makeCard(
      cells[resolvedLabelIndex] || "Not specified",
      metric?.value || "",
      metric?.header || "",
      details,
      `${context} ${headers.join(" ")}`,
    )];
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

export function parseViewResults(viewResults) {
  return viewResults.map((viewResult) => {
    const sections = splitResultsMarkdown(viewResult.markdown || "");
    const summary = sections?.summary || "";
    const results = sections?.results || viewResult.markdown || "";
    return {
      ...viewResult,
      summary,
      results,
      cards: parseResultCards(results, `${viewResult.display_name} ${summary}`),
    };
  });
}