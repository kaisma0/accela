#!/usr/bin/env bash
set -eu

    #Headcrab Compatibile Client Version
    HeadcrabCompatibleClientVer=1773426488
    
    #Paths
    SCRIPT_DIR="$(dirname "$(realpath "$0")")"
    SteamInstallDir=$HOME/.steam/steam
    FlatpakSteamInstallDir=$HOME/.var/app/com.valvesoftware.Steam/.steam/steam
    FlatpakSLSsteamInstallDir=$HOME/.var/app/com.valvesoftware.Steam/.local/share/SLSsteam
    FlatpakSLSsteamConfigDir=$HOME/.var/app/com.valvesoftware.Steam/.config/SLSsteam
    SLSsteamInstallDir=$HOME/.local/share/SLSsteam
    SLSsteamConfigDir=$HOME/.config/SLSsteam
    InstallDir=$SCRIPT_DIR/bin
    Headcrab_Downgrader_Path=$HOME/.headcrab
    SLS_VERSION=""
	
	#URL'S
    Headcrab_Downgrade_URL="http://localhost:1666/"
	LinuxClientManifest="https://raw.githubusercontent.com/Deadboy666/SteamTracking/refs/heads/headcrab-testing/ClientManifest/steam_client_ubuntu12"
    DeckClientManifest="https://raw.githubusercontent.com/Deadboy666/SteamTracking/refs/heads/headcrab-testing/ClientManifest/steam_client_steamdeck_stable_ubuntu12"
	Headcrab_Native="https://raw.githubusercontent.com/Deadboy666/h3adcr-b-modul3s/refs/heads/main/headcrab_native.sh"
	Headcrab_Flatpak="https://raw.githubusercontent.com/Deadboy666/h3adcr-b-modul3s/refs/heads/main/headcrab_flatpak.sh"
    dgsc="https://github.com/Deadboy666/h3adcr-b/raw/refs/heads/testing/dgsc"
    dlm="https://github.com/Deadboy666/h3adcr-b/raw/refs/heads/testing/dlm"
    Sources="https://raw.githubusercontent.com/Deadboy666/h3adcr-b-modul3s/refs/heads/main/sources.txt"
	
    read_os_release(){
        local f
        OS_ID=""
        OS_ID_LIKE=""
        for f in /etc/os-release /usr/lib/os-release; do
            [ -r "$f" ] || continue
            . "$f"
            break
        done
        OS_ID=${ID:-}
        OS_ID_LIKE=${ID_LIKE:-}
    }

    archcheck(){
        read_os_release
        case " $OS_ID $OS_ID_LIKE " in
            *" arch "*|*" cachyos "*) return 0 ;;
        esac
        return 1
        }

    debiancheck(){
        read_os_release
        case " $OS_ID $OS_ID_LIKE " in
            *" debian "*|*" ubuntu "*) return 0 ;;
        esac
        return 1
        }   

    steamoscheck(){
        read_os_release
        [ "$OS_ID" = "steamos" ]
        }
		
	bazzitecheck(){
        read_os_release
        [ "$OS_ID" = "bazzite" ]
        }
    
    flatpakcheck(){
        [ -d "$FlatpakSteamInstallDir" ]
        }
		

    SteamOSClientCheck(){
        if [ -f "steam_client_steamdeck_stable_ubuntu12.manifest" ]; then
            versionnumber=$(grep '"version"' steam_client_steamdeck_stable_ubuntu12.manifest | awk -F'"' '{print $4}')
            echo "SteamClientChannel: Stable"
        else
            versionnumber=$(grep '"version"' steam_client_steamdeck_publicbeta_ubuntu12.manifest | awk -F'"' '{print $4}')
            echo "SteamClientChannel: Beta"
        fi
            echo "SteamClientType: SteamOS"
        }
		
	BazziteClientCheck(){
        if [ -f "steam_client_steamdeck_stable_ubuntu12.manifest" ]; then
            versionnumber=$(grep '"version"' steam_client_steamdeck_stable_ubuntu12.manifest | awk -F'"' '{print $4}')
            echo "SteamClientChannel: Stable (Bazzite-Deck)"
        elif [ -f steam_client_steamdeck_publicbeta_ubuntu12.manifest ]; then
            versionnumber=$(grep '"version"' steam_client_steamdeck_publicbeta_ubuntu12.manifest | awk -F'"' '{print $4}')
            echo "SteamClientChannel: Beta (Bazzite-Deck)"
		elif [ -f "steam_client_ubuntu12.manifest" ]; then
            versionnumber=$(grep '"version"' steam_client_ubuntu12.manifest | awk -F'"' '{print $4}')
            echo "SteamClientChannel: Stable (Bazzite-Desktop)"
		else
            versionnumber=$(grep '"version"' steam_client_publicbeta_ubuntu12.manifest | awk -F'"' '{print $4}')
            echo "SteamClientChannel: Beta (Bazzite-Desktop)"
        fi
            echo "SteamClientType: Bazzite"
        }

    FlatpakClientCheck(){
        if [ -f "steam_client_ubuntu12.manifest" ]; then
            versionnumber=$(grep '"version"' steam_client_ubuntu12.manifest | awk -F'"' '{print $4}')
            echo "SteamClientChannel: Stable"
        else
            versionnumber=$(grep '"version"' steam_client_publicbeta_ubuntu12.manifest | awk -F'"' '{print $4}')
            echo "SteamClientChannel: Beta"
        fi
            echo "SteamClientType: Flatpak"
        }

    NativeClientCheck(){
        if [ -f "steam_client_ubuntu12.manifest" ]; then
            versionnumber=$(grep '"version"' steam_client_ubuntu12.manifest | awk -F'"' '{print $4}')
            echo "SteamClientChannel: Stable"
        else
            versionnumber=$(grep '"version"' steam_client_publicbeta_ubuntu12.manifest | awk -F'"' '{print $4}')
            echo "SteamClientChannel: Beta"
        fi
            echo "SteamClientType: Native"
        }

    CheckClientInfo(){
        echo "SteamClientInfo:"
        wheresteamcfg
        cd package/
        if steamoscheck; then
            SteamOSClientCheck
		elif bazzitecheck; then
            BazziteClientCheck
        elif flatpakcheck; then
            FlatpakClientCheck
        else
            NativeClientCheck
        fi
            echo "SteamClientVersion: $versionnumber"
            }
    
    CheckHeadcrabCompatibility(){
            echo "=================================================="
        CheckClientInfo
        if [[ "$versionnumber" == "$HeadcrabCompatibleClientVer" ]]; then
            echo "ClientCompatCheck: SteamClientVersion Compatible"
            echo "================================================="
            clientinstall
        else
            echo "ClientCompatCheck: SteamClientVersion Incompatible"
            echo "=================================================="
            echo "Bootstrapping Injector"
            clientdowngrade
        fi
        }

    preinstallchecks(){
        InstallDebianDeps
        RemoveArchPkg
        DisableSLSsteamPath
        }

    InstallDebianDeps() {	    
	    if debiancheck; then

		if apt-cache search --names-only '^libcurl4t64$' | grep -q "libcurl4t64"; then
		    pkg_name="libcurl4t64"
		else
		    pkg_name="libcurl4"
		fi
		target_pkg="${pkg_name}:i386"

		if dpkg -s "$target_pkg" >/dev/null 2>&1; then
		    echo -e "$target_pkg already installed"
		    return 0
		fi

		if ! dpkg --print-foreign-architectures | grep -q "i386"; then
		    echo "Adding i386 architecture..."
		    sudo dpkg --add-architecture i386
		    sudo apt-get update >/dev/null 2>&1
		fi

		if sudo apt-get install -y "$target_pkg" >/dev/null 2>&1; then
		    echo -e "$target_pkg installed successfully"
		else
		    echo -e "$target_pkg failed to install"
		fi

        fi
	    }

    RemoveArchPkg(){
        if archcheck; then
        installed_pkgs=$(pacman -Qq | grep -E '^slssteam(-git)?$' || true)
        if [ -n "$installed_pkgs" ]; then
            echo "Headcrab Will Transition To The Install To One That Can Seemlessly Update."
			echo "This Will Replace The System Package Of SLSsteam With One That Is Local."
            echo "Uninstalling Arch packages: $installed_pkgs"
            sudo pacman -Rns --noconfirm $installed_pkgs
        fi
        fi
    }

    DisableSLSsteamPath(){
        local local_target="$SLSsteamInstallDir/path/steam"
        local flatpak_target="$FlatpakSLSsteamInstallDir/path/steam"
        local acted=0

        if [ -e "$flatpak_target" ]; then
            echo "Found: $flatpak_target"
            echo "Renaming $flatpak_target -> ${flatpak_target}.bak"
            mv -- "$flatpak_target" "${flatpak_target}.bak"
            acted=1
        fi

        if [ -e "$local_target" ]; then
            echo "Found: $local_target"
            echo "Renaming $local_target -> ${local_target}.bak"
            mv -- "$local_target" "${local_target}.bak"
            acted=1
        fi

        if [ "$acted" -eq 0 ]; then
            echo "Not present: $flatpak_target"
            echo "Not present: $local_target"
        fi
    }
	
    TrashiteWatMani(){
		wheresteamcfg
		cd package/
		if [ -f "steam_client_steamdeck_stable_ubuntu12.installed"]; then
			echo "Headcrab Downloading Bazzite-Deck Client Manifest"
			wget "$DeckClientManifest" &> /dev/null
		else
			echo "Headcrab Downloading Bazzite-Desktop Client Manifest"
			wget "$LinuxClientManifest" &> /dev/null
		fi
			echo "" &> /dev/null
		}
		
    DownloadClientManifest(){
	    if steamoscheck; then
	        echo "Headcrab Downloading Steamos Client Manifest.."
	        wget "$DeckClientManifest" &> /dev/null
		elif bazzitecheck; then
			TrashiteWatMani
	    else
	        echo "Headcrab Downloading Linux Client Manifest.."
	        wget "$LinuxClientManifest" &> /dev/null
	    fi
	        echo "Client Manifest Downloaded"
    }
    
    download_dgsc(){
        mkdir -p $Headcrab_Downgrader_Path
        cd $Headcrab_Downgrader_Path/
        if [ -f "$Headcrab_Downgrader_Path/dgsc" ]; then
            echo "Headcrab_dgsc Downloaded Already."
        else
            echo "Downloading Headcrab_dgsc.."
            wget "$dgsc" &> /dev/null
            chmod +x dgsc
        fi
          echo "" &> /dev/null
        }
        
        download_dlm(){
        mkdir -p $Headcrab_Downgrader_Path
        cd $Headcrab_Downgrader_Path/
        if [ -f "$Headcrab_Downgrader_Path/dlm" ]; then
            echo "Headcrab_dlm Downloaded Already."
        else
            echo "Downloading Headcrab_dlm.."
            wget "$dlm" &> /dev/null
            chmod +x dlm
        fi
          echo "" &> /dev/null
        }
        
        dlm(){
        download_dlm
        echo "Running Fetching Client Update Headcrab_dlm.."
        wheresteamcfg
        cd package/
        $Headcrab_Downgrader_Path/dlm --input-file sources.txt --max-concurrent 16
        echo "Headcrab_dlm Fetched Client Update"
        }
        
    dgsc(){
        download_dgsc
        echo "Running Headcrab_dgsc.."
        wheresteamcfg
        cd package/
        $Headcrab_Downgrader_Path/dgsc --port 1666 --silent & sleep 1s "$@"
        }
        
    prepdowngrade(){
        wheresteamcfg
        rm package/*
        cd package/
        wget "$Sources" &> /dev/null
        DownloadClientManifest
        dlm
        }
        
    clientinstall(){
        echo "the headcrab latches on the steam process.."
		createsteamcfg
        if steamoscheck; then
            echo "Steamos Detected"
            echo "Headcrab Bootstrapping SLSsteam.."
           export_sls wheresteam -exitsteam
		elif bazzitecheck; then
			echo "Bazzite Detected"
            echo "Headcrab Bootstrapping SLSsteam.."
           export_sls wheresteam -exitsteam
        elif flatpakcheck; then
            echo "Headcrab Bootstrapping SLSsteam.."
            export_sls wheresteam -clearbeta steam://exit
		else
			export_sls wheresteam -clearbeta -exitsteam &> /dev/null
        fi
            echo "" &> /dev/null
            }
        
    clientdowngrade(){
        prepdowngrade
        overideupdate
        }
        
    nuketheclient(){
                killall steam | true
            }
        
    wheresteam(){
        if [ -d "$FlatpakSteamInstallDir" ]; then
                flatpak run com.valvesoftware.Steam "$@"
        else
                steam "$@"
            fi
                echo "" &> /dev/null
            }
            
    wheresteamdir(){
        if [ -d "$FlatpakSteamInstallDir" ]; then
                mkdir -p $FlatpakSLSsteamInstallDir
                cp -f $InstallDir/library-inject.so $FlatpakSLSsteamInstallDir/
                cp -f $InstallDir/SLSsteam.so $FlatpakSLSsteamInstallDir/ 
        else
                 mkdir -p $SLSsteamInstallDir
                 mkdir -p $SLSsteamConfigDir
                 cp -f $InstallDir/library-inject.so $SLSsteamInstallDir/
                 cp -f $InstallDir/SLSsteam.so $SLSsteamInstallDir/
            fi
                echo "" &> /dev/null
            }
            
    wheresteamcfg(){
        if [ -d "$FlatpakSteamInstallDir" ]; then
               cd $FlatpakSteamInstallDir/
        else
                cd $SteamInstallDir/
            fi
                echo "" &> /dev/null
            }

    whereSLSsteamconfig(){
        if [ -d "$FlatpakSLSsteamConfigDir" ]; then
               mkdir -p $FlatpakSLSsteamConfigDir
               cd $FlatpakSLSsteamConfigDir/
        else
                mkdir -p $SLSsteamConfigDir
                cd $SLSsteamConfigDir/
            fi
                echo "" &> /dev/null
            }
            
    overideupdate(){
        echo "the headcrab latches on the steam process.."
        if steamoscheck; then
            echo "Steamos Detected"
            createsteamcfg
            dgsc
            echo "Headcrab Connecting to The Updater.."
           export_sls wheresteam -textmode -forcesteamupdate -forcepackagedownload -overridepackageurl "$Headcrab_Downgrade_URL" -exitsteam &> /dev/null
		elif bazzitecheck; then
			echo "Bazzite Detected"
            createsteamcfg
            dgsc
            echo "Headcrab Connecting to The Updater.."
           export_sls wheresteam -textmode -forcesteamupdate -forcepackagedownload -overridepackageurl "$Headcrab_Downgrade_URL" -exitsteam &> /dev/null
        else
            createsteamcfg
            dgsc
            echo "Headcrab Connecting to The Updater.."
            export_sls wheresteam -clearbeta -textmode -forcesteamupdate -forcepackagedownload -overridepackageurl "$Headcrab_Downgrade_URL" -exitsteam &> /dev/null
        fi
            killall dgsc
            echo "Compatible Update Applied Via Headcrab_dgsc"
            }
            
    checkforsteamcfg(){
    echo "the headcrab approaches.."
    wheresteamcfg
    if [ -f "steam.cfg" ]; then
        rm steam.cfg
    else
        echo "No Pre Exisiting Steam.cfg"
    fi
        nuketheclient
        CheckHeadcrabCompatibility
        conditioncheck
        }


    downloadSLSsteam(){
        echo "Downloading Latest SLSsteam.."
        cd $SCRIPT_DIR/
        local release_json
        release_json=$(curl -s "https://api.github.com/repos/AceSLS/SLSsteam/releases/latest")
        SLS_VERSION=$(printf '%s' "$release_json" | grep '"tag_name"' | head -1 | cut -d '"' -f 4)
        local download_url
        download_url=$(printf '%s' "$release_json" | grep 'browser_download_url' | grep 'SLSsteam-Any.7z' | cut -d '"' -f 4)
        wget -O SLSsteam-Any.7z "$download_url" &> /dev/null
    }

    saveVersion(){
        [ -z "${SLS_VERSION:-}" ] && return
        if [ -d "$FlatpakSteamInstallDir" ]; then
            printf '%s' "$SLS_VERSION" > "$FlatpakSLSsteamInstallDir/VERSION"
        else
            printf '%s' "$SLS_VERSION" > "$SLSsteamInstallDir/VERSION"
        fi
        echo "SLSsteam Version Saved: $SLS_VERSION"
    }
    
    export_sls(){
        if [ -d "$FlatpakSteamInstallDir" ]; then
                copySLSsteam
                LD_AUDIT=$HOME/.var/app/com.valvesoftware.Steam/.local/share/SLSsteam/library-inject.so:$HOME/.var/app/com.valvesoftware.Steam/.local/share/SLSsteam/SLSsteam.so "$@"
        else
                copySLSsteam
                LD_AUDIT=$HOME/.local/share/SLSsteam/library-inject.so:$HOME/.local/share/SLSsteam/SLSsteam.so "$@"
        fi
                echo "" &> /dev/null
                }

    extractSLSsteam(){
        downloadSLSsteam
         7z x $SCRIPT_DIR/SLSsteam-Any.7z -aoa > /dev/null
         rm -rf tools
         rm -rf res
         rm setup.sh
         rm -rf docs
         rm SLSsteam-Any.7z
         echo "SLSsteam Downloaded: Latest"
         }

    copySLSsteam(){
        extractSLSsteam
        wheresteamdir
        rm -rf $InstallDir
        }

    InstallSLSsteam(){
        echo "Installing SLSsteam..."
        copySLSsteam
        saveVersion
        backupconfig
        }

    plsdontbreakthingsthatwork(){
        whereSLSsteamconfig
        if [ -f "config.yaml.bak" ]; then
            mv config.yaml.bak config.yaml
    else
            echo "" &> /dev/null
        fi
            echo "" &> /dev/null
            }
            
    backupconfig(){
        plsdontbreakthingsthatwork
        if [ -f "config.yaml" ]; then
            mv config.yaml config.yaml.bak
    else
            echo "" &> /dev/null
        fi
            echo "" &> /dev/null
            }

    editconfig(){
    whereSLSsteamconfig
        if grep -q -F "SafeMode: no" "config.yaml"; then
            sed -i "s/^SafeMode:.*/SafeMode: yes/" config.yaml
        fi
        echo "SafeMode: Enabled"
        }

    createsteamcfg(){
    wheresteamcfg
    if [ -f "steam.cfg" ]; then
        rm steam.cfg
    else
        cat << 'EOF' > steam.cfg
BootStrapperInhibitAll=enable
BootStrapperForceSelfUpdate=disable
EOF
    fi
        echo "" &> /dev/null
    }

    patchsteam(){
        if [ -d "$FlatpakSteamInstallDir" ]; then
                patchflatpaksteam
        else
                patchlocalsteam
        fi
        }

        
    patchflatpaksteam(){
        cd $FlatpakSteamInstallDir/
        if [ -f "steam.sh" ]; then
            mv steam.sh steam.sh.bak
        	wget -O steam.sh "$Headcrab_Flatpak" &> /dev/null
			chmod +x steam.sh
		fi
            echo "SLSSteamInstallType: Flatpak"
        }

    patchlocalsteam(){
        cd $SteamInstallDir/
        if [ -f "steam.sh" ]; then
            mv steam.sh steam.sh.bak
        	wget -O steam.sh "$Headcrab_Native" &> /dev/null
			chmod +x steam.sh
		fi
        	echo "SLSSteamInstallType: Local"
        }

        conditioncheck(){
            echo "Checking Conditions..."
            patchsteam
            echo "BlockedClientUpdates: Enabled"
            editconfig
            echo "HeadcrabStatus: Patched"
            }

    main(){
        preinstallchecks
        backupconfig
        checkforsteamcfg
        }

    main
