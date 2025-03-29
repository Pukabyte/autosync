// Main JavaScript for Autosync frontend

document.addEventListener('DOMContentLoaded', function() {
    // Dark mode functionality
    const darkModeToggle = document.getElementById('darkModeToggle');
    const prefersDarkScheme = window.matchMedia('(prefers-color-scheme: dark)');
    
    // Check for saved dark mode preference
    const savedDarkMode = localStorage.getItem('darkMode');
    if (savedDarkMode === 'true') {
        document.body.classList.add('dark-mode');
        darkModeToggle.innerHTML = '<i class="bi bi-sun"></i>';
    } else if (savedDarkMode === 'false') {
        document.body.classList.remove('dark-mode');
        darkModeToggle.innerHTML = '<i class="bi bi-moon-stars"></i>';
    } else {
        // If no preference saved, use system preference
        if (prefersDarkScheme.matches) {
            document.body.classList.add('dark-mode');
            darkModeToggle.innerHTML = '<i class="bi bi-sun"></i>';
            localStorage.setItem('darkMode', 'true');
        } else {
            document.body.classList.remove('dark-mode');
            darkModeToggle.innerHTML = '<i class="bi bi-moon-stars"></i>';
            localStorage.setItem('darkMode', 'false');
        }
    }
    
    // Handle dark mode toggle
    darkModeToggle.addEventListener('click', function() {
        document.body.classList.toggle('dark-mode');
        if (document.body.classList.contains('dark-mode')) {
            darkModeToggle.innerHTML = '<i class="bi bi-sun"></i>';
            localStorage.setItem('darkMode', 'true');
        } else {
            darkModeToggle.innerHTML = '<i class="bi bi-moon-stars"></i>';
            localStorage.setItem('darkMode', 'false');
        }
    });
    
    // Listen for system dark mode changes
    prefersDarkScheme.addEventListener('change', function(e) {
        if (!localStorage.getItem('darkMode')) {
            if (e.matches) {
                document.body.classList.add('dark-mode');
                darkModeToggle.innerHTML = '<i class="bi bi-sun"></i>';
            } else {
                document.body.classList.remove('dark-mode');
                darkModeToggle.innerHTML = '<i class="bi bi-moon-stars"></i>';
            }
        }
    });

    // Auto-dismiss alerts after 5 seconds
    setTimeout(function() {
        const alerts = document.querySelectorAll('.alert');
        alerts.forEach(function(alert) {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        });
    }, 5000);

    // Initialize any tooltips
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Initialize any popovers
    const popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    popoverTriggerList.map(function (popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });
}); 