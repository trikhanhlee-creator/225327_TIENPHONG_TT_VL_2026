document.addEventListener('DOMContentLoaded', () => {
    const revealTargets = document.querySelectorAll('[data-reveal]');
    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries, obs) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) {
                    return;
                }
                entry.target.classList.add('is-visible');
                obs.unobserve(entry.target);
            });
        }, { threshold: 0.14 });

        revealTargets.forEach((target) => observer.observe(target));
    } else {
        revealTargets.forEach((target) => target.classList.add('is-visible'));
    }

    const usageModeHint = document.getElementById('usageModeHint');
    const authStatusText = document.getElementById('authStatusText');

    const setGuestMode = () => {
        if (usageModeHint) {
            usageModeHint.textContent = 'Bạn cần đăng nhập để sử dụng các tính năng Word, Excel và Composer.';
        }
        if (authStatusText) {
            authStatusText.textContent = 'Khách truy cập: vui lòng đăng nhập trước khi bắt đầu thao tác.';
        }
    };

    fetch('/api/auth/session', {
        method: 'GET',
        credentials: 'include',
        cache: 'no-store',
    })
        .then((response) => response.ok ? response.json() : { authenticated: false })
        .then((data) => {
            const isAuthenticated = Boolean(data && data.authenticated && data.user);
            if (!isAuthenticated) {
                setGuestMode();
                return;
            }

            const displayName = data.user.username || data.user.email || 'Tài khoản';
            if (usageModeHint) {
                usageModeHint.textContent = 'Đang đăng nhập: ' + displayName + '. Lịch sử và phiên làm việc sẽ đồng bộ theo tài khoản.';
            }
            if (authStatusText) {
                authStatusText.textContent = data.user.is_admin
                    ? 'Admin đang đăng nhập: có đầy đủ quyền quản trị hệ thống.'
                    : 'Người dùng đang đăng nhập: đã bật lưu lịch sử cá nhân.';
            }
        })
        .catch(() => {
            setGuestMode();
        });
});
