from app.services.backup import perform_backup


async def run_scheduled_backup(ctx) -> None:
    """Cron job (daily) and also enqueable ad-hoc for the "run backup now"
    button in Settings, both paths call the same perform_backup()."""
    await perform_backup()
