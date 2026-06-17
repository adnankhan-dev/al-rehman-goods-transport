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

function bindSearchableSelects() {
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
                return option.value !== "" && option.value !== "0";
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
            select.dispatchEvent(new Event("change", { bubbles: true }));
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
                select.value = "0";
                input.setCustomValidity(allowEmpty ? "" : "Select a value from the list.");
            } else {
                const matched = findExactMatch(rawValue);
                if (matched) {
                    select.value = matched.value;
                    input.setCustomValidity("");
                } else {
                    select.value = "0";
                    input.setCustomValidity(allowEmpty ? "" : "Select a value from the list.");
                }
            }
            if (select.value !== previousValue) {
                select.dispatchEvent(new Event("change", { bubbles: true }));
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

        input.value = selectedOptionLabel();
        syncSelectFromText();
        if (allowEmpty && !input.value.trim()) {
            input.setCustomValidity("");
        }
    });
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
        bindTableTools();
        bindSearchableSelects();
    });
} else {
    bindTableTools();
    bindSearchableSelects();
}
