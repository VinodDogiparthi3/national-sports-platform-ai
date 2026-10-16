export const TIP_ROTATION_MS = 5000;

// Only statements that are true of this app; no invented facts or numbers.
export const waitingTips = {
  en: [
    "Tip: add “export to Excel” to a question to get a downloadable report.",
    "Tip: you can ask in English or Arabic.",
    "Tip: try comparing regions, sports or federations.",
    "Tip: open the source details under an answer to see the dataset and query used.",
  ],
  ar: [
    "نصيحة: أضف «تصدير إلى Excel» إلى سؤالك للحصول على تقرير قابل للتنزيل.",
    "نصيحة: يمكنك السؤال بالعربية أو الإنجليزية.",
    "نصيحة: جرّب مقارنة المناطق أو الرياضات أو الاتحادات.",
    "نصيحة: افتح تفاصيل المصدر أسفل الإجابة لمعرفة مجموعة البيانات والاستعلام المستخدم.",
  ],
};

export function stepState(stepIndex, currentIndex) {
  if (stepIndex < currentIndex) return "done";
  return stepIndex === currentIndex ? "active" : "pending";
}

export function nextTipIndex(current, count) {
  return count > 0 ? (current + 1) % count : 0;
}
