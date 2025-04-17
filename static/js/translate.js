let currentLanguage = 'ru'; // язык по умолчанию

function loadTranslations(language) {
    fetch(`/static/locales/${language}.json`)
        .then(response => response.json())
        .then(translations => {
            document.querySelectorAll('[data-i18n]').forEach(el => {
                
                const innerContent = el.innerHTML.trim();

                if (el.classList.contains('no-translate')) {
                    return;
                }
                if (el.id && el.id.startsWith('no-translate')) {
                    return;
                }
                if (innerContent.includes('{{') && innerContent.includes('}}')) {
                    return;
                }

                const key = el.getAttribute('data-i18n');
                
                if (translations[key]) {
                    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                        el.value = translations[key];
                    } else {
                        el.innerHTML = translations[key];
                    }
                }
            });
        })
        .catch(error => console.error('Ошибка загрузки перевода:', error));
}

function changeLanguage(language) {
    currentLanguage = language;
    localStorage.setItem('preferredLanguage', language); // <-- Сохраняем выбранный язык
    loadTranslations(language);
}

document.addEventListener('DOMContentLoaded', function() {
    // Сначала смотрим есть ли язык в localStorage
    const savedLang = localStorage.getItem('preferredLanguage');

    if (savedLang) {
        currentLanguage = savedLang;
    } else {
        // Если нет в localStorage, смотрим язык браузера
        const browserLang = navigator.language || navigator.userLanguage;
        if (browserLang.startsWith('kk') || browserLang.startsWith('kz')) {
            currentLanguage = 'kz';
        } else if (browserLang.startsWith('en')) {
            currentLanguage = 'en';
        } else {
            currentLanguage = 'ru';
        }
    }

    loadTranslations(currentLanguage);

    document.querySelectorAll('[data-lang]').forEach(btn => {
        btn.addEventListener('click', () => {
            changeLanguage(btn.getAttribute('data-lang'));
        });
    });
});
