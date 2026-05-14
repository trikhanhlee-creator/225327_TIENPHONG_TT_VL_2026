function isProtectedAppPath(pathname) {
	if (!pathname) {
		return false;
	}
	if (pathname.startsWith('/admin-')) {
		return true;
	}
	const exactProtectedPaths = new Set([
		'/home',
		'/form',
		'/excel',
		'/word-upload',
		'/composer',
		'/payment',
		'/user-account',
	]);
	if (exactProtectedPaths.has(pathname)) {
		return true;
	}
	return pathname.startsWith('/excel-form/') || pathname.startsWith('/excel-data-form/');
}

function buildLandingRedirectUrl() {
	return '/';
}

async function revalidateAuthAfterHistoryRestore() {
	const pathname = window.location.pathname;
	if (!isProtectedAppPath(pathname)) {
		return;
	}

	try {
		const response = await fetch('/api/auth/check-auth', {
			method: 'GET',
			credentials: 'include',
			cache: 'no-store',
		});
		const data = response.ok ? await response.json() : { authenticated: false };
		const isAuthenticated = Boolean(data && data.authenticated && data.user);

		if (!isAuthenticated) {
			window.location.replace(buildLandingRedirectUrl());
			return;
		}

		if (pathname.startsWith('/admin-') && !data.user.is_admin) {
			window.location.replace('/');
		}
	} catch (_error) {
		window.location.replace(buildLandingRedirectUrl());
	}
}

document.addEventListener('DOMContentLoaded', () => {
	void revalidateAuthAfterHistoryRestore();

	window.addEventListener('pageshow', (event) => {
		if (event && event.persisted) {
			void revalidateAuthAfterHistoryRestore();
		}
	});

	const toggleButtons = document.querySelectorAll('[data-theme-toggle]');

	if (
		toggleButtons.length > 0
		&& window.themeManager
		&& typeof window.themeManager.getCurrentTheme === 'function'
		&& typeof window.themeManager.updateThemeUI === 'function'
	) {
		window.themeManager.updateThemeUI(window.themeManager.getCurrentTheme());
	} else if (toggleButtons.length > 0) {
		toggleButtons.forEach((toggleButton) => {
			if (toggleButton.dataset.themeBound === '1') {
				return;
			}

			toggleButton.addEventListener('click', (event) => {
				event.preventDefault();
				const isLight = document.body.classList.contains('light-mode');
				document.body.classList.toggle('light-mode', !isLight);
				document.body.classList.toggle('dark-mode', isLight);
				localStorage.setItem('app-theme', isLight ? 'dark' : 'light');

				const icon = toggleButton.querySelector('[data-theme-icon]');
				const label = toggleButton.querySelector('[data-theme-label]');
				if (icon) {
					icon.textContent = isLight ? '🌙' : '☀️';
				}
				if (label) {
					label.textContent = isLight ? 'Tối' : 'Sáng';
				}
			});

			toggleButton.dataset.themeBound = '1';
		});
	}

	const pathname = window.location.pathname;
	const navLinks = document.querySelectorAll('.main-nav .nav-link');
	const navDropdowns = document.querySelectorAll('[data-nav-dropdown]');
	const navDropdownToggles = document.querySelectorAll('[data-nav-dropdown-toggle]');

	navLinks.forEach((link) => {
		const href = link.getAttribute('href');
		if (!href) {
			return;
		}
		if (href.startsWith('#')) {
			const target = document.querySelector(href);
			const isSectionVisible = target && Math.abs(target.getBoundingClientRect().top) < 160;
			link.classList.toggle('active', Boolean(isSectionVisible));
			return;
		}

		const isHomeHref = href === '/' || href === '/home';
		const isOnHome = pathname === '/' || pathname === '/home';
		const isActive = (isHomeHref && isOnHome) || (!isHomeHref && pathname.startsWith(href));
		link.classList.toggle('active', isActive);
	});

	const closeAllNavDropdowns = () => {
		navDropdowns.forEach((dropdown) => {
			dropdown.classList.remove('open');
		});
		navDropdownToggles.forEach((button) => {
			button.setAttribute('aria-expanded', 'false');
		});
	};

	navDropdownToggles.forEach((button) => {
		button.addEventListener('click', (event) => {
			event.preventDefault();
			event.stopPropagation();

			const dropdown = button.closest('[data-nav-dropdown]');
			if (!dropdown) {
				return;
			}

			const shouldOpen = !dropdown.classList.contains('open');
			closeAllNavDropdowns();
			if (shouldOpen) {
				dropdown.classList.add('open');
				button.setAttribute('aria-expanded', 'true');
			}
		});
	});

	document.addEventListener('click', (event) => {
		if (!event.target.closest('[data-nav-dropdown]')) {
			closeAllNavDropdowns();
		}
	});

	if (pathname.startsWith('/admin-')) {
		navDropdownToggles.forEach((button) => {
			button.classList.add('active');
		});
	}

	const adminLinks = document.querySelectorAll('[data-admin-link]');
	const anonOnlyItems = document.querySelectorAll('[data-auth-anon]');
	const authOnlyItems = document.querySelectorAll('[data-auth-user]');
	const accountNameItems = document.querySelectorAll('[data-account-name]');
	const accountLinkItems = document.querySelectorAll('[data-account-link]');
	const adminUserOnlyItems = document.querySelectorAll('[data-admin-user-only]');
	const accountDropdowns = document.querySelectorAll('[data-account-dropdown]');
	const accountToggleButtons = document.querySelectorAll('[data-account-toggle]');
	const accountLogoutButtons = document.querySelectorAll('[data-account-logout]');

	if (adminLinks.length === 0 && anonOnlyItems.length === 0 && authOnlyItems.length === 0) {
		return;
	}

	const closeAllAccountMenus = () => {
		accountDropdowns.forEach((dropdown) => {
			dropdown.classList.remove('open');
		});
		accountToggleButtons.forEach((button) => {
			button.setAttribute('aria-expanded', 'false');
		});
	};

	accountToggleButtons.forEach((button) => {
		button.addEventListener('click', (event) => {
			event.preventDefault();
			event.stopPropagation();

			const container = button.closest('[data-account-dropdown]');
			if (!container) {
				return;
			}

			const shouldOpen = !container.classList.contains('open');
			closeAllAccountMenus();
			if (shouldOpen) {
				container.classList.add('open');
				button.setAttribute('aria-expanded', 'true');
			}
		});
	});

	document.addEventListener('click', (event) => {
		if (!event.target.closest('[data-account-dropdown]')) {
			closeAllAccountMenus();
		}
	});

	document.addEventListener('keydown', (event) => {
		if (event.key === 'Escape') {
			closeAllAccountMenus();
			closeAllNavDropdowns();
		}
	});

	accountLogoutButtons.forEach((button) => {
		button.addEventListener('click', async (event) => {
			event.preventDefault();
			button.disabled = true;
			button.textContent = 'Đăng xuất...';

			try {
				await fetch('/api/auth/logout', {
					method: 'POST',
					credentials: 'include',
					cache: 'no-store',
				});
			} finally {
				window.location.replace('/');
			}
		});
	});

	adminLinks.forEach((link) => {
		link.style.display = 'none';
	});

	authOnlyItems.forEach((item) => {
		item.style.display = 'none';
	});

	adminUserOnlyItems.forEach((item) => {
		item.style.display = 'none';
	});

	fetch('/api/auth/check-auth', {
		method: 'GET',
		credentials: 'include',
	})
		.then((response) => response.ok ? response.json() : { authenticated: false })
		.then((data) => {
			const isAuthenticated = Boolean(data && data.authenticated && data.user);

			if (isAuthenticated) {
				const accountLabel = data.user.username || data.user.email || 'Tài khoản';
				anonOnlyItems.forEach((item) => {
					item.style.display = 'none';
				});
				authOnlyItems.forEach((item) => {
					item.style.display = '';
				});
				accountNameItems.forEach((item) => {
					item.textContent = accountLabel;
				});
				accountLinkItems.forEach((item) => {
					item.setAttribute('href', '/user-account');
				});
				adminUserOnlyItems.forEach((item) => {
					item.style.display = data.user.is_admin ? '' : 'none';
				});
			} else {
				closeAllAccountMenus();
				anonOnlyItems.forEach((item) => {
					item.style.display = '';
				});
				authOnlyItems.forEach((item) => {
					item.style.display = 'none';
				});
				adminUserOnlyItems.forEach((item) => {
					item.style.display = 'none';
				});
			}

			if (isAuthenticated && data.user.is_admin) {
				adminLinks.forEach((link) => {
					link.style.display = '';
				});
			}
		})
		.catch(() => {
			anonOnlyItems.forEach((item) => {
				item.style.display = '';
			});
			authOnlyItems.forEach((item) => {
				item.style.display = 'none';
			});
			adminUserOnlyItems.forEach((item) => {
				item.style.display = 'none';
			});
		});
});
