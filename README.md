    # SigmaOS-asdcontrol-git

    ASD control via git-tracked config

    Part of the **SigmaOS** sovereign operating-system ecosystem — a multi-repository
    distribution modelled on Linux Mint and Omarchy Linux. See the parent
    [`SigmaOS`](https://github.com/AaryanSinghChauhan09/SigmaOS) repository for the
    full component index.

    ## Upstream inspirations
    - https://github.com/omacom/asdcontrol-git

    ## Design principles applied
    - **Separation of Concerns**
- **Defensive Programming**
- **Fail-Fast**
- **KISS**
- **DRY**
- **Deterministic Testing**

    ## Usage
    Verbs: `get, set, sync-plan, status`

    ```
    $ sigma-asd get brightness --config asd.conf
80
$ sigma-asd set mode night --config asd.conf
mode=night
$ sigma-asd sync-plan --repo ~/dotfiles --message "asd: night mode"
$ sigma-asd status --config asd.conf --repo ~/dotfiles

    ```

    ## Build & test
    ```
    make build
    make test
    ```

    ## Licence
    GNU General Public License v3.0 (FOSS).
