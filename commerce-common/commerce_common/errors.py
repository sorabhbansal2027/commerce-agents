# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Shared error sentinels used across merchant and shopping agent packages."""


class NotOffered(Exception):
    """Raised by a backend method when the capability is not available for this deployment."""
