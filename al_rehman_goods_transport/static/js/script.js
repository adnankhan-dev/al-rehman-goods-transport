function bindTableTools() {
    document.querySelectorAll("[data-table-filter]").forEach(function (input) {
        if (input.dataset.tableToolsBound === "true") {
            return;
        }
        input.dataset.tableToolsBound = "true";

        const table = document.getElementById(input.dataset.tableFilter);
        if (!table) {
            return;
        }

        const body = table.tBodies && table.tBodies.length ? table.tBodies[0] : null;
        if (!body) {
            return;
        }

        const rows = Array.from(body.querySelectorAll("tr"));

        function applyFilter() {
            const term = (input.value || "").trim().toLowerCase();
            let visibleRows = 0;

            rows.forEach(function (row) {
                const isEmptyRow = !!row.querySelector("td[colspan]");
                if (isEmptyRow) {
                    row.hidden = false;
                    return;
                }

                const rowText = (row.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
                const isVisible = !term || rowText.includes(term);
                row.hidden = !isVisible;
                if (isVisible) {
                    visibleRows += 1;
                }
            });

            const emptyMessageRow = rows.find(function (row) {
                return !!row.querySelector("td[colspan]");
            });
            if (emptyMessageRow && term) {
                emptyMessageRow.hidden = visibleRows !== 0;
            }
        }

        input.addEventListener("input", applyFilter);
        input.addEventListener("search", applyFilter);
        applyFilter();
    });

    document.querySelectorAll("[data-table-print]").forEach(function (button) {
        if (button.dataset.tablePrintBound === "true") {
            return;
        }
        button.dataset.tablePrintBound = "true";
        button.addEventListener("click", function (event) {
            event.preventDefault();
            window.print();
        });
    });
}

// Dropdowns are turned into the same type-to-search control the order form
// uses, on forms AND on list-page filter bars.
//
// Two rules decide, so nothing has to be tagged by hand:
//   1. The select picks a record. Every such field is named after its foreign
//      key and ends in "_id" (contractor_id, vehicle_id, site_id, owner_id …),
//      while fixed choice lists never do (kind, entity_type, billing_status,
//      report_type, role, payment_method). Those lists grow over time, so they
//      are enhanced even while they are still short.
//   2. Anything else long enough to be tedious to scroll.
// Override per element with data-searchable="true" / "false".
const SEARCHABLE_AUTO_THRESHOLD = 10;

function isRecordPicker(select) {
    return /_id$/.test(select.id || "") || /_id$/.test(select.name || "");
}

function shouldAutoEnhance(select) {
    const explicit = select.dataset.searchable;
    if (explicit === "false") {
        return false;
    }
    if (explicit === "true") {
        return true;
    }
    if (select.multiple || select.disabled) {
        return false;
    }
    // Already wired by the render_searchable_select macro.
    if (select.id && document.querySelector('[data-searchable-select-target="' + select.id + '"]')) {
        return false;
    }
    // A picker with nothing but its placeholder gains nothing from a search box.
    if (select.options.length < 2) {
        return false;
    }
    return isRecordPicker(select) || select.options.length >= SEARCHABLE_AUTO_THRESHOLD;
}

function isPlaceholderOption(option) {
    return option.value === "" || option.value === "0";
}

// Build the combobox markup around an existing <select> so filter bars and any
// other plain dropdown get the same behaviour without template changes.
function enhanceSelect(select) {
    if (!select.id) {
        select.id = "sel_" + Math.random().toString(36).slice(2, 10);
    }

    const hasPlaceholder = Array.from(select.options).some(isPlaceholderOption);
    const selectedOption = select.options[select.selectedIndex];
    const currentLabel = selectedOption && !isPlaceholderOption(selectedOption)
        ? selectedOption.textContent.trim()
        : "";

    const wrapper = document.createElement("div");
    wrapper.className = "searchable-select-control";

    const input = document.createElement("input");
    input.type = "text";
    input.id = select.id + "_search";
    input.className = "form-control searchable-select-input";
    input.value = currentLabel;
    input.placeholder = select.dataset.searchablePlaceholder || "Type to search";
    input.autocomplete = "off";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.dataset.searchableSelectInput = "true";
    input.dataset.searchableSelectTarget = select.id;
    input.dataset.searchableSelectAllowEmpty = hasPlaceholder ? "true" : "false";

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "searchable-select-toggle";
    toggle.tabIndex = -1;
    toggle.setAttribute("aria-label", "Show all options");
    toggle.dataset.searchableSelectToggle = select.id;
    toggle.innerHTML = '<i class="bi bi-chevron-down" aria-hidden="true"></i>';

    const menu = document.createElement("ul");
    menu.className = "searchable-select-menu";
    menu.id = select.id + "_menu";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;

    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(input);
    wrapper.appendChild(toggle);
    wrapper.appendChild(menu);
    wrapper.appendChild(select);
    select.classList.add("d-none");

    return input;
}

function bindSearchableSelects() {
    document.querySelectorAll("select").forEach(function (select) {
        if (shouldAutoEnhance(select)) {
            enhanceSelect(select);
        }
    });

    document.querySelectorAll("[data-searchable-select-input='true']").forEach(function (input) {
        if (input.dataset.searchableSelectBound === "true") {
            return;
        }

        const select = document.getElementById(input.dataset.searchableSelectTarget);
        const menu = document.getElementById(input.dataset.searchableSelectTarget + "_menu");
        if (!select || !menu) {
            return;
        }

        input.dataset.searchableSelectBound = "true";
        const allowEmpty = input.dataset.searchableSelectAllowEmpty === "true";
        let activeIndex = -1;
        let visibleOptions = [];
        let selfDispatching = false;

        function fireChange() {
            selfDispatching = true;
            select.dispatchEvent(new Event("change", { bubbles: true }));
            selfDispatching = false;
        }

        // The "nothing chosen" option is "0" on WTForms pickers but "" on the
        // list-page filter bars, so clearing must restore whichever this select
        // actually has — otherwise the field submits nothing at all.
        function emptyValue() {
            const placeholder = Array.from(select.options).find(function (option) {
                return option.value === "" || option.value === "0";
            });
            return placeholder ? placeholder.value : "";
        }

        // Render the menu on <body> with fixed positioning so it can never be
        // clipped by a parent with overflow:hidden (the form card, scroll areas).
        document.body.appendChild(menu);

        function positionMenu() {
            const rect = input.getBoundingClientRect();
            menu.style.top = rect.bottom + 4 + "px";
            menu.style.left = rect.left + "px";
            menu.style.width = rect.width + "px";
        }

        const toggleButton = document.querySelector(
            '[data-searchable-select-toggle="' + input.dataset.searchableSelectTarget + '"]'
        );
        if (toggleButton) {
            toggleButton.addEventListener("mousedown", function (event) {
                event.preventDefault(); // keep focus on the input
                if (menu.hidden) {
                    input.focus();
                    openAll();
                } else {
                    closeMenu();
                }
            });
        }

        function selectableOptions() {
            return Array.from(select.options).filter(function (option) {
                if (option.value === "" || option.value === "0") {
                    return false;
                }
                // Dependent filters (owner -> vehicle, contractor -> site) narrow
                // a list by hiding/disabling options rather than removing them,
                // so those must not be offered here either.
                return !option.hidden && !option.disabled;
            });
        }

        function selectedOptionLabel() {
            const selectedOption = select.options[select.selectedIndex];
            if (!selectedOption || selectedOption.value === "" || selectedOption.value === "0") {
                return "";
            }
            return selectedOption.textContent.trim();
        }

        function findExactMatch(term) {
            const normalizedTerm = (term || "").trim().toLowerCase();
            if (!normalizedTerm) {
                return null;
            }
            return selectableOptions().find(function (option) {
                return option.textContent.trim().toLowerCase() === normalizedTerm;
            }) || null;
        }

        function closeMenu() {
            menu.hidden = true;
            menu.innerHTML = "";
            activeIndex = -1;
            visibleOptions = [];
            input.setAttribute("aria-expanded", "false");
        }

        function commit(option) {
            select.value = option.value;
            input.value = option.textContent.trim();
            input.setCustomValidity("");
            closeMenu();
            fireChange();
        }

        function highlight(index) {
            const items = Array.from(menu.querySelectorAll(".searchable-select-option"));
            items.forEach(function (item, i) {
                item.classList.toggle("is-active", i === index);
            });
            if (index >= 0 && items[index]) {
                items[index].scrollIntoView({ block: "nearest" });
            }
            activeIndex = index;
        }

        function showOptions(options, emptyLabel) {
            visibleOptions = options;
            menu.innerHTML = "";
            if (!options.length) {
                const empty = document.createElement("li");
                empty.className = "searchable-select-empty";
                empty.textContent = emptyLabel;
                menu.appendChild(empty);
                positionMenu();
                menu.hidden = false;
                input.setAttribute("aria-expanded", "true");
                activeIndex = -1;
                return;
            }
            options.forEach(function (option, i) {
                const item = document.createElement("li");
                item.className = "searchable-select-option";
                item.setAttribute("role", "option");
                item.textContent = option.textContent.trim();
                item.addEventListener("mousedown", function (event) {
                    event.preventDefault();
                    commit(option);
                });
                item.addEventListener("mouseenter", function () {
                    highlight(i);
                });
                menu.appendChild(item);
            });
            positionMenu();
            menu.hidden = false;
            input.setAttribute("aria-expanded", "true");
            highlight(0);
        }

        function openAll() {
            // Show the complete list (caret button, lone ".", or ArrowDown).
            showOptions(selectableOptions(), "No options available");
        }

        function openMenu(term) {
            const normalizedTerm = (term || "").trim().toLowerCase();
            // Only show matches once the user has started typing. A lone "." or
            // "*" is treated as "show me everything".
            if (!normalizedTerm) {
                closeMenu();
                return;
            }
            if (normalizedTerm === "." || normalizedTerm === "*") {
                openAll();
                return;
            }
            showOptions(
                selectableOptions().filter(function (option) {
                    return option.textContent.trim().toLowerCase().includes(normalizedTerm);
                }),
                "No matches"
            );
        }

        function syncSelectFromText() {
            // Keep the hidden select in sync with free text (no menu interaction).
            const rawValue = (input.value || "").trim();
            const previousValue = select.value;
            if (!rawValue) {
                select.value = emptyValue();
                input.setCustomValidity(allowEmpty ? "" : "Select a value from the list.");
            } else {
                const matched = findExactMatch(rawValue);
                if (matched) {
                    select.value = matched.value;
                    input.setCustomValidity("");
                } else {
                    select.value = emptyValue();
                    input.setCustomValidity(allowEmpty ? "" : "Select a value from the list.");
                }
            }
            if (select.value !== previousValue) {
                fireChange();
            }
        }

        input.addEventListener("input", function () {
            openMenu(input.value);
            syncSelectFromText();
        });

        input.addEventListener("focus", function () {
            // Open only if the user already has text — never dump the whole list on a bare click.
            if (input.value.trim()) {
                openMenu(input.value);
            }
        });

        input.addEventListener("keydown", function (event) {
            if (menu.hidden) {
                if (event.key === "ArrowDown") {
                    // ArrowDown opens the full list (or the filtered list if typing).
                    if (input.value.trim()) {
                        openMenu(input.value);
                    } else {
                        openAll();
                    }
                    event.preventDefault();
                }
                return;
            }
            if (event.key === "ArrowDown") {
                event.preventDefault();
                highlight(Math.min(activeIndex + 1, visibleOptions.length - 1));
            } else if (event.key === "ArrowUp") {
                event.preventDefault();
                highlight(Math.max(activeIndex - 1, 0));
            } else if (event.key === "Enter") {
                if (activeIndex >= 0 && visibleOptions[activeIndex]) {
                    event.preventDefault();
                    commit(visibleOptions[activeIndex]);
                }
            } else if (event.key === "Escape") {
                closeMenu();
            }
        });

        input.addEventListener("blur", function () {
            // Delay so a menu click registers first.
            window.setTimeout(function () {
                closeMenu();
                syncSelectFromText();
            }, 150);
        });

        // Keep the fixed menu aligned with the input while scrolling/resizing.
        window.addEventListener("scroll", function () {
            if (!menu.hidden) {
                positionMenu();
            }
        }, true);
        window.addEventListener("resize", function () {
            if (!menu.hidden) {
                positionMenu();
            }
        });

        input.searchableSelect = {
            refresh: function (clearInput) {
                const selectedLabel = selectedOptionLabel();
                input.value = selectedLabel || (clearInput ? "" : input.value);
                closeMenu();
                syncSelectFromText();
                if (allowEmpty && !selectedLabel && !input.value.trim()) {
                    input.setCustomValidity("");
                }
            },
        };

        // Keep the visible text honest when something else changes the select —
        // a dependent filter clearing an now-invalid choice, or a script
        // rebuilding the option list.
        select.addEventListener("change", function () {
            // Ignore the change events this control fires itself — otherwise a
            // partially-typed term (which legitimately matches nothing yet)
            // would be wiped on every keystroke.
            if (selfDispatching) {
                return;
            }
            const label = selectedOptionLabel();
            if (label !== input.value) {
                input.value = label;
                input.setCustomValidity(allowEmpty || label ? "" : "Select a value from the list.");
            }
        });

        input.value = selectedOptionLabel();
        syncSelectFromText();
        if (allowEmpty && !input.value.trim()) {
            input.setCustomValidity("");
        }
    });
}

// Money inputs: a fixed, uneditable "Rs." sits in front of the field and the
// number groups itself with commas while it is typed. The grouped text is
// stripped back to a plain number on submit (MoneyField also strips it
// server-side, so a paste or a JS-less browser still posts a valid amount).
function groupDigits(whole) {
    return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function formatMoneyText(raw) {
    // Keep only digits and the first decimal point.
    let cleaned = String(raw).replace(/[^\d.]/g, "");
    const firstDot = cleaned.indexOf(".");
    if (firstDot !== -1) {
        cleaned = cleaned.slice(0, firstDot + 1) + cleaned.slice(firstDot + 1).replace(/\./g, "");
    }
    if (!cleaned) {
        return "";
    }
    const parts = cleaned.split(".");
    const grouped = groupDigits(parts[0]);
    // Money is never more precise than paisa.
    return parts.length > 1 ? grouped + "." + parts[1].slice(0, 2) : grouped;
}

function bindMoneyInputs() {
    document.querySelectorAll("[data-money='true']").forEach(function (input) {
        if (input.dataset.moneyBound === "true") {
            return;
        }
        input.dataset.moneyBound = "true";

        // A number input rejects commas outright, so switch to text and keep the
        // numeric keypad on mobile.
        input.type = "text";
        input.setAttribute("inputmode", "decimal");

        if (!input.parentElement.classList.contains("money-field")) {
            const wrapper = document.createElement("div");
            wrapper.className = "money-field";
            const prefix = document.createElement("span");
            prefix.className = "money-field-prefix";
            prefix.setAttribute("aria-hidden", "true");
            prefix.textContent = "Rs.";
            input.parentNode.insertBefore(wrapper, input);
            wrapper.appendChild(prefix);
            wrapper.appendChild(input);
            input.classList.add("money-field-input");
        }

        input.addEventListener("input", function () {
            // Preserve the caret: count the digits before it, then put it back
            // after the same digit once separators shift around.
            const start = input.selectionStart;
            const digitsBefore = (input.value.slice(0, start).match(/[\d.]/g) || []).length;
            input.value = formatMoneyText(input.value);
            let seen = 0;
            let caret = input.value.length;
            for (let i = 0; i < input.value.length; i += 1) {
                if (/[\d.]/.test(input.value[i])) {
                    seen += 1;
                }
                if (seen === digitsBefore) {
                    caret = i + 1;
                    break;
                }
            }
            if (digitsBefore === 0) {
                caret = 0;
            }
            input.setSelectionRange(caret, caret);
        });

        input.value = formatMoneyText(input.value);

        const form = input.form;
        if (form && form.dataset.moneySubmitBound !== "true") {
            form.dataset.moneySubmitBound = "true";
            form.addEventListener("submit", function () {
                form.querySelectorAll("[data-money='true']").forEach(function (field) {
                    field.value = field.value.replace(/,/g, "");
                });
            });
        }
    });
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
        bindTableTools();
        bindSearchableSelects();
        bindMoneyInputs();
    });
} else {
    bindTableTools();
    bindSearchableSelects();
    bindMoneyInputs();
}
