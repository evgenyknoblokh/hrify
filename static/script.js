/*
 * script.js — i18n + UX (устойчивый к изменению разметки)
 */

let I18N = null;
let currentLang = "ru";

document.addEventListener("DOMContentLoaded", async () => {
    // базовые элементы (могут отсутствовать на некоторых страницах)
    const textarea = document.getElementById("userText");
    const resultDiv = document.getElementById("result");
    const resultWrap = document.getElementById("resultWrap");
    const clearBtn = document.getElementById("clearBtn");
    const langSelect = document.getElementById("langSelect");
    const copyBtn = document.getElementById("copyBtn");

    // 1) Загрузка словаря
    try {
        I18N = await fetch("/static/i18n.json", { cache: "no-store" }).then(r => r.json());
    } catch (e) {
        console.error("Failed to load i18n.json", e);
        I18N = null;
    }

    // 2) Определение языка и инициализация UI
    const saved = localStorage.getItem("hrify_lang");
    const browserLang = (navigator.language || "ru").slice(0, 2).toLowerCase();
    currentLang = saved || (["ru", "en", "es"].includes(browserLang) ? browserLang : "ru");

    // если есть селектор — синхронизируем значение и навешиваем обработчик
    if (langSelect) {
        langSelect.value = currentLang;
        langSelect.addEventListener("change", () => {
            currentLang = langSelect.value;
            localStorage.setItem("hrify_lang", currentLang);
            applyTranslations();
        });
    }

    applyTranslations(); // применим переводы к странице сразу

    // 3) Кнопки сценариев (если это главная страница)
    const scenarioButtons = document.querySelectorAll(".btn-reject, .btn-hire, .btn-remind");
    if (scenarioButtons.length && textarea && resultDiv) {
        scenarioButtons.forEach(button => {
            button.addEventListener("click", () => handleScenario(button.getAttribute("data-scenario")));
        });
    }

    // 4) Очистка текста
    if (clearBtn && textarea && resultWrap && resultDiv) {
        clearBtn.addEventListener("click", () => {
            textarea.value = "";
            hideResult();
        });
    }

    // 5) Копирование результата (если кнопка есть)
    if (copyBtn && resultDiv) {
        copyBtn.addEventListener("click", () => {
            const text = resultDiv.textContent || "";
            if (!text.trim()) return;
            navigator.clipboard.writeText(text).catch(err => console.warn("Copy failed", err));
        });
    }

    // ===== helpers =====

    function t(key) {
        if (!I18N) return key;
        return (I18N[currentLang] && I18N[currentLang][key]) || key;
    }

    function applyTranslations() {
        try {
            // data-i18n
            document.querySelectorAll("[data-i18n]").forEach(el => {
                const key = el.getAttribute("data-i18n");
                const text = t(key);
                if (text) el.textContent = text;
            });

            // placeholders
            document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
                const key = el.getAttribute("data-i18n-placeholder");
                const text = t(key);
                if (text) el.setAttribute("placeholder", text);
            });

            // <html lang="">
            document.documentElement.setAttribute("lang", currentLang);

            // <title>
            const titleKey = "title";
            if (I18N && I18N[currentLang] && I18N[currentLang][titleKey]) {
                document.title = I18N[currentLang][titleKey];
            }
        } catch (e) {
            console.warn("applyTranslations failed:", e);
        }
    }

    function showResult(message, isError = false, isFinal = false) {
        if (!resultDiv || !resultWrap) return;
        resultDiv.textContent = message || "";
        resultDiv.classList.toggle("error", !!isError);
        resultWrap.style.display = "block";

        // финал считаем только если есть непустой текст и нет ошибки
        const hasText = !!(message && String(message).trim());
        const finalOk = isFinal && !isError && hasText;
        resultWrap.classList.toggle("is-final", finalOk);

        // НЕ трогаем copyBtn.style.display — это делает только CSS по классу .is-final
    }

    function hideResult() {
        if (!resultDiv || !resultWrap) return;
        resultDiv.textContent = "";
        resultDiv.classList.remove("error");
        resultWrap.style.display = "none";
        resultWrap.classList.remove("is-final");
    }

    // лёгкая предвалидация (клиент)
    function looksLikeJunk(text) {
        const t = (text || "").trim();
        if (t.length < 12) return t("enterText"); // коротко — попросим ввести текст
        const uniq = new Set(t).size;
        if (uniq <= 3) return "Похоже на повторяющиеся символы.";
        const letters = (t.match(/\p{L}/gu) || []).length;
        if (letters / t.length < 0.55) return "Слишком мало буквенных символов.";
        const symbols = (t.match(/[^\p{L}\p{N}\s]/gu) || []).length;
        if (symbols / t.length > 0.35) return "Слишком много специальных символов.";
        return null;
    }

    async function handleScenario(scenario) {
        if (!textarea) return;
        const text = textarea.value.trim();
        if (!text) {
            showResult(t("enterText"), true);
            return;
        }
        const quick = looksLikeJunk(text);
        if (quick) { showResult(quick, true); return; }

        disableScenarioButtons(true);
        showResult(t("processing"), false, false);

        try {
            const resp = await fetch("/process", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text, scenario, ui_lang: currentLang }),
            });
            const data = await resp.json();
            if (data.error) showResult(data.error, true, false);
            else showResult(data.result || "", false, true);
        } catch (err) {
            console.error(err);
            showResult(t("errorFetch"), true);
        } finally {
            disableScenarioButtons(false);
        }
    }

    function disableScenarioButtons(disabled) {
        document.querySelectorAll(".btn-reject, .btn-hire, .btn-remind").forEach(btn => {
            btn.disabled = !!disabled;
        });
    }
});
