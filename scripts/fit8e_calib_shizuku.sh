#!/system/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Run with Shizuku/ADB shell. The desktop client uses this same transaction.
panel_key=ar14_1080p_144hz_video_mode_dsi_panel
panel_file=qdcm_calib_data_ar14_1080p_144hz_video_mode_dsi_panel.json
display_dir=/data/vendor/display
target="$display_dir/$panel_file"
state_dir="$display_dir/.fit8e-colorcalib"
record="$state_dir/record"
original="$state_dir/original"
umask 077

die() { printf '%s\n' "ERROR: $2" >&2; exit "$1"; }
digest() {
    line=$(sha256sum "$1") || return 1
    printf '%s\n' "${line%% *}"
}
legacy_builtin() {
    # v1.1.0 was distributed with both Windows and Unix line endings.
    case "$1" in
        3eb02edeb1dfb62032a1f13843ce94547fcc636391967735e6d3c40fe90aa219|13baac65dce0dfc1de09fc29fd6d6d5e478943aa6e0f231f519772f3d5475e63) return 0 ;;
        *) return 1 ;;
    esac
}
validate_profile() {
    LC_ALL=C awk -v panel="$panel_key" '
# AOSP ships awk. Read JSON structurally, including the byte-swapped QDCM tables.
function fail(msg) { gsub(SUBSEP,"/",msg); print "Invalid QDCM: " msg > "/dev/stderr"; exit 1 }
function space() { while (substr(doc,pos,1) ~ /^[ \t\r\n]$/) pos++ }
function string_value(    start,n,part,c,e,h,i,code) {
    start=++pos
    n=index(substr(doc,start),"\"")
    if (!n) fail("unterminated string")
    part=substr(doc,start,n-1)
    if (part !~ /[\\[:cntrl:]]/) { pos+=n; return part }
    part=""
    while (pos<=length(doc)) {
        c=substr(doc,pos++,1)
        if (c=="\"") return part
        if (c ~ /[[:cntrl:]]/) fail("control character in string")
        if (c=="\\") {
            e=substr(doc,pos++,1)
            if (e=="u") {
                h=toupper(substr(doc,pos,4))
                if (length(h)!=4 || h ~ /[^0-9A-F]/) fail("invalid Unicode escape")
                pos+=4
                code=0
                for (i=1;i<=4;i++) code=code*16+index("0123456789ABCDEF",substr(h,i,1))-1
                c=(code<128 ? sprintf("%c",code) : "\\u" h)
            } else {
                if (!(e in escapes)) fail("invalid escape")
                c=escapes[e]
            }
        }
        part=part c
    }
    fail("unterminated string")
}
function value(path,depth,    c,key,i,closechar,literal) {
    if (depth>96 || path in kind) fail("duplicate or excessively nested field")
    space(); c=substr(doc,pos,1)
    if (c=="{" || c=="[") {
        kind[path]=(c=="{" ? "object":"array"); closechar=(c=="{" ? "}":"]")
        pos++; space()
        if (substr(doc,pos,1)==closechar) { pos++; return }
        for (i=0;;i++) {
            space()
            if (closechar=="}") {
                if (substr(doc,pos,1)!="\"") fail("expected object key")
                key=string_value()
                if (key ~ /[[:cntrl:]]/) fail("control character in key")
                space(); if (substr(doc,pos++,1)!=":") fail("expected colon")
            } else key=i
            value(path SUBSEP key,depth+1)
            space(); c=substr(doc,pos++,1)
            if (c==closechar) return
            if (c!=",") fail("expected separator")
        }
    }
    if (c=="\"") { kind[path]="string"; val[path]=string_value(); return }
    if (!match(substr(doc,pos),/^(true|false|null|-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?)/)) fail("invalid value")
    literal=substr(doc,pos,RLENGTH); pos+=RLENGTH
    kind[path]=(literal ~ /^[-0-9]/ ? "number" : "literal"); val[path]=literal
}
function parse(input,path) {
    doc=input; pos=1; value(path,0); space()
    if (pos<=length(doc) || kind[path]!="object") fail("expected one JSON object")
}
function field(path,type) {
    if (kind[path]!=type) fail("missing or mistyped " path)
    return val[path]
}
function decode(path,prefix,    h,out,i,n) {
    h=toupper(field(path,"string")); n=length(h)
    if (!n || n%2 || h ~ /[^0-9A-F]/) fail("invalid encoded table")
    out=""; i=1
    if (n%4) { out=octet[substr(h,1,2)]; i=3 }
    for (;i<=n;i+=4) out=out octet[substr(h,i+2,2)] octet[substr(h,i,2)]
    parse(out,prefix)
}
function table(path,count,limit,    csv,a,i,n) {
    if (checked[path]) return
    csv=field(path,"string"); sub(/,$/,"",csv)
    n=split(csv,a,",")
    if (n!=count) fail("wrong LUT length at " path)
    for (i=1;i<=n;i++) if (a[i] !~ /^[0-9]+$/ || a[i]+0>limit) fail("out-of-range LUT sample")
    checked[path]=1
}
function check_block(name,count,limit,    base,m,bind,ref,i,mode) {
    base="shared" SUBSEP name
    decode("profile" SUBSEP "Common" SUBSEP name,base)
    if (field(base SUBSEP "numEntries","number")+0!=count) fail(name " size")
    for (i=1;i<=4;i++) {
        mode=modes[i]; m="profile" SUBSEP panel SUBSEP mode; bind="binding" SUBSEP mode SUBSEP name
        decode(m SUBSEP name,bind)
        if (field(bind SUBSEP "enable","literal")!="true" || field(bind SUBSEP "numEntries","number")+0!=count) fail(mode " " name)
        if (name=="PostBlendGamut") {
            ref=field(bind SUBSEP "Custom","number")
            if (ref !~ /^[0-9]+$/) fail("invalid gamut index")
            table(base SUBSEP "Custom" SUBSEP ref,count*3,limit)
        } else {
            if (field(bind SUBSEP "HPM","literal")!="true") fail("HPM disabled")
            ref=field(bind SUBSEP "Predefined","string")
            table(base SUBSEP "Predefined" SUBSEP ref,count,limit)
        }
    }
}
BEGIN {
    for (i=0;i<=255;i++) octet[sprintf("%02X",i)]=sprintf("%c",i)
    escapes["\""]="\""; escapes["\\"]="\\"; escapes["/"]="/"
    escapes["b"]="\b"; escapes["f"]="\f"; escapes["n"]="\n"; escapes["r"]="\r"; escapes["t"]="\t"
    split("sRGB P3 HDR Native",modes)
}
{ data=data $0 "\n" }
END {
    parse(data,"profile")
    if (field("profile" SUBSEP "Version","number")+0!=3) fail("requires Version 3")
    check_block("PostBlendIGC",385,65535)
    check_block("PostBlendGC",1280,1023)
    check_block("PostBlendGamut",4913,4095)
    decode("profile" SUBSEP panel SUBSEP "HDR" SUBSEP "PostBlendHdrBlob","hdr")
    if (!length(field("hdr" SUBSEP "blob","string"))) fail("missing HDR configuration")
}
' "$1"
}

# E-EDID base blocks have four 18-byte descriptor slots. Only monitor names
# and panel-like text descriptors identify a panel. Ignore serial numbers.
edid_panel_name() {
    od -A n -v -t u1 -N 128 "$1" 2>/dev/null | LC_ALL=C awk '
        { for (i=1; i<=NF; i++) { byte[n++]=$i+0; sum+=$i } }
        END {
            if (n!=128 || sum%256 || byte[0]!=0 || byte[7]!=0) exit
            for (i=1; i<=6; i++) if (byte[i]!=255) exit
            for (d=54; d<=108; d+=18) {
                if (byte[d] || byte[d+1] || byte[d+2] || byte[d+4]) continue
                tag=byte[d+3]
                if (tag!=252 && tag!=254) continue
                name=""
                for (i=d+5; i<d+18; i++) {
                    c=byte[i]
                    if (c==0 || c==10) break
                    if (c<32 || c>126) { name=""; break }
                    name=name sprintf("%c",c)
                }
                sub(/ +$/,"",name)
                if (tag==252 && name!="") monitor=name
                if (tag==254 && tolower(name) ~ /^ar[0-9]/) panel_text=name
            }
            if (monitor!="") print monitor
            else if (panel_text!="") print panel_text
        }'
}

active_panel() {
    panel=''; panel_source=''; panel_timing=''
    for connector in /sys/class/drm/card*-DSI-1; do
        [ -d "$connector" ] || continue
        if [ -r "$connector/status" ] && [ "$(cat "$connector/status")" != connected ]; then continue; fi
        for attr in panel_name panel_info msm_fb_panel_info; do
            [ -r "$connector/$attr" ] || continue
            if [ "$attr" = panel_name ]; then
                panel=$(head -n 1 "$connector/$attr")
            else
                panel=$(sed -n 's/^[[:space:]]*panel_name[[:space:]]*[=:][[:space:]]*//p' "$connector/$attr" | head -n 1)
            fi
            if [ -n "$panel" ]; then panel_source="DSI-1/$attr"; return; fi
        done
    done
    for prop in vendor.display.primary_panel ro.boot.primary_panel; do
        panel=$(getprop "$prop")
        if [ -n "$panel" ]; then panel_source=$prop; return; fi
    done
    if [ -r /sys/class/graphics/fb0/msm_fb_panel_info ]; then
        panel=$(sed -n 's/^[[:space:]]*panel_name[[:space:]]*[=:][[:space:]]*//p' /sys/class/graphics/fb0/msm_fb_panel_info | head -n 1)
        if [ -n "$panel" ]; then panel_source=fb0/msm_fb_panel_info; return; fi
    fi
    for connector in /sys/class/drm/card*-DSI-1; do
        [ -r "$connector/edid" ] && [ "$(cat "$connector/status" 2>/dev/null)" = connected ] || continue
        panel=$(edid_panel_name "$connector/edid")
        [ -n "$panel" ] || continue
        panel_source=DSI-1/edid
        if grep -qx '1080x1920x144vid' "$connector/modes" 2>/dev/null; then panel_timing=1080x1920x144vid; fi
        return
    done
}
identify() {
    dev=$(getprop ro.product.device) || die 2 "Device identity unavailable"
    product=$(getprop ro.product.name)
    soc=$(getprop ro.soc.model)
    model_ok=0; hardware_ok=0
    for code in "$dev" "$product"; do
        case "$code" in PocketFITElite|AYANEO_Pocket_FIT_Elite) model_ok=1 ;; esac
    done
    case "$soc" in SM8750P|SM8750) hardware_ok=$model_ok ;; esac
    active_panel
    label=$(printf '%s\n' "$panel" | tr '[:upper:]' '[:lower:]' | tr ' ,-' '___' 2>/dev/null)
    panel_state=mismatch
    case "$label" in
        '') panel_state=unknown ;;
        ar14|ar14_1080p_144hz|"$panel_key"|mdss_dsi_"$panel_key"|qcom_mdss_dsi_"$panel_key") panel_state=match ;;
        ar14_1080p_14) if [ "$panel_source" = DSI-1/edid ]; then panel_state=match; fi ;;
    esac
    # The 13-byte EDID string can end in "14". Confirm 144 Hz on the same connector.
    if [ "$panel_source" = DSI-1/edid ] && [ "$panel_state" = match ] && [ -z "$panel_timing" ]; then
        panel_state=unknown
    fi
}
require_target() {
    identify
    [ "$hardware_ok" = 1 ] || die 2 "Unexpected device/SoC: $dev / $soc"
    case "$panel_state/$force" in
        match/*|unknown/--force) return ;;
        unknown/*) die 2 "Could not confirm the ar14 144 Hz panel. Verify the panel before using --force." ;;
        *) die 2 "Unexpected primary panel: $panel" ;;
    esac
}
probe() {
    identify
    printf 'MODEL=%s\nDEVICE=%s\nPRODUCT=%s\nSOC=%s\nBOARD=%s\nBUILD=%s\n' \
        "$(getprop ro.product.model)" "$dev" "$product" "$soc" "$(getprop ro.board.platform)" "$(getprop ro.build.display.id)"
    printf 'PANEL=%s\nPANEL_SOURCE=%s\nPANEL_TIMING=%s\nPANEL_STATE=%s\n' "$panel" "$panel_source" "$panel_timing" "$panel_state"
    printf 'MODEL_OK=%s\nHARDWARE_OK=%s\nSYSMODE=%s\n' "$model_ok" "$hardware_ok" "$(settings get system display_color_mode)"
    sum=$(md5sum "$target" 2>/dev/null) || sum=''
    root_tool=none
    if command -v xsu >/dev/null 2>&1; then root_tool=xsu
    elif command -v su >/dev/null 2>&1; then root_tool=su; fi
    printf 'DATA_MD5=%s\nROOTTOOL=%s\n---SF---\n' "${sum%% *}" "$root_tool"
    dumpsys SurfaceFlinger 2>/dev/null | grep -E 'DisplayColorSetting:|Current color mode:|ColorMode::' | head -n 12
}
setting() {
    previous=$(settings get system display_color_mode) || return 1
    printf '%s\n' "$previous" | grep -Eq '^(null|-?[0-9]+)$' || return 1
    printf '%s\n' "$previous"
}
set_checked() {
    if [ "$1" = null ]; then
        settings delete system display_color_mode >/dev/null || return 1
    else
        settings put system display_color_mode "$1" || return 1
    fi
    observed=$(setting) && [ "$observed" = "$1" ]
}
target_hash() {
    [ ! -L "$target" ] || return 1
    if [ -f "$target" ]; then digest "$target"
    elif [ ! -e "$target" ]; then printf '%s\n' '-'
    else return 1; fi
}
is_hash_or_empty() { printf '%s\n' "$1" | grep -Eq '^(-|[0-9a-f]{64})$'; }
save_record() {
    # Journal the old and intended digests before the atomic rename. Either is
    # recoverable if Android stops the process between the two renames.
    printf '%s %s %s %s\n' "$base" "$base_mode" "$1" "$2" > "$state_dir/record.next" &&
        mv -f "$state_dir/record.next" "$record" || die 73 "Cannot save installation record"
}
read_record() {
    IFS=' ' read -r base base_mode old pending extra < "$record" || die 3 "Unreadable installation record"
    [ -z "$extra" ] && is_hash_or_empty "$base" && is_hash_or_empty "$old" &&
        is_hash_or_empty "$pending" && printf '%s\n' "$base_mode" | grep -Eq '^(null|-?[0-9]+)$' ||
        die 3 "Damaged installation record"
    if [ "$current" != "$old" ] && [ "$current" != "$pending" ]; then
        die 3 "The active file changed outside this tool. It was preserved."
    fi
    if [ "$base" != '-' ]; then
        backup_hash=$(digest "$original") && [ "$backup_hash" = "$base" ] || die 3 "Original backup is missing or damaged"
    fi
}
new_stage() {
    stage=$(mktemp "$display_dir/.fit8e-stage.XXXXXX") || die 73 "Cannot stage in the display directory"
}
copy_original() {
    # Preserve mode, timestamps and ownership, or stop before replacing anything.
    cp -p "$1" "$2" && copied=$(digest "$2") && [ "$copied" = "$base" ] &&
        [ "$(stat -c '%u:%g:%a' "$1")" = "$(stat -c '%u:%g:%a' "$2")" ] ||
        die 73 "Could not preserve the original calibration file or its permissions"
    source_label=$(ls -Zd "$1") || die 73 "Cannot read original file label"
    dest_label=$(ls -Zd "$2") || die 73 "Cannot read backup file label"
    if [ "${source_label%% *}" != "${dest_label%% *}" ]; then
        chcon "${source_label%% *}" "$2" || die 73 "Cannot preserve original SELinux label"
    fi
}
install_profile() {
    [ -f "$src" ] || die 3 "Calibration profile not found: $src"
    before=$(digest "$src") || die 3 "Cannot read input profile"
    [ -z "$expected" ] || [ "$before" = "$expected" ] || die 3 "Transfer SHA-256 mismatch; active file preserved"
    new_stage
    cp "$src" "$stage" || die 73 "Cannot copy staged profile"
    want=$(digest "$stage") && [ "$want" = "$before" ] || die 3 "Staged profile checksum mismatch"
    legacy_builtin "$want" || validate_profile "$stage" || die 3 "Staged profile failed V3 validation"
    chmod 0644 "$stage" || die 73 "Cannot set profile permissions"
    if [ "$(id -u)" = 0 ]; then
        chown system:graphics "$stage" && chcon u:object_r:vendor_display_vendor_data_file:s0 "$stage" ||
            die 3 "Could not apply display ownership or SELinux label"
    fi
    if [ -f "$record" ]; then
        read_record
    else
        base_mode=$(setting) || die 3 "Cannot capture original display mode"
        base=$current
        if [ "$base" != '-' ]; then
            copy_original "$target" "$state_dir/original.next"
            mv -f "$state_dir/original.next" "$original" || die 73 "Cannot save original backup"
        fi
    fi
    save_record "$current" "$want"
    mv -f "$stage" "$target" || die 73 "Could not activate staged profile"
    stage=''
    save_record "$want" "$want"
    set_checked 0 || die 4 "Profile installed, but Natural mode could not be verified. Retry before rebooting."
    printf '%s\n' "Profile installed; SHA-256 and mode 0 verified. Reboot to activate."
}
uninstall_profile() {
    if [ -f "$record" ]; then
        read_record
    elif [ "$current" = '-' ]; then
        printf '%s\n' 'UNCHANGED: no installed profile.'
        return
    else
        if ! legacy_builtin "$current"; then
            [ -n "$proof" ] && [ -f "$proof" ] || die 3 "Untracked profile preserved. Supply its exact legacy JSON to remove it."
            known=$(digest "$proof") && [ "$known" = "$current" ] && validate_profile "$proof" ||
                die 3 "Legacy JSON does not establish ownership; current profile preserved"
        fi
        base='-'; base_mode=0
    fi
    if [ "$base" != '-' ] && [ "$current" != "$base" ]; then
        new_stage
        copy_original "$original" "$stage"
    fi
    save_record "$current" "$base"
    if [ "$base" = '-' ]; then
        rm -f "$target" || die 73 "Cannot remove managed profile"
    elif [ -n "$stage" ]; then
        mv -f "$stage" "$target" || die 73 "Cannot restore original profile"
        stage=''
    fi
    set_checked "$base_mode" || die 4 "File restored, but previous display mode could not be verified. Retry uninstall."
    rm -f "$record" || die 73 "Settings restored, but could not clear the installation record"
    rm -f "$original" || die 73 "Settings restored, but could not clear the saved backup"
    rmdir "$state_dir" 2>/dev/null || :  # Only remove an empty state directory.
    printf '%s\n' "Previous file and display mode restored. Reboot to activate."
}
op=${1:-status}
shift || :
src=''; proof=''; expected=''; force=''; mode=''
case "$op" in
    validate) [ "$#" = 1 ] || die 1 "Usage: validate profile.json"; validate_profile "$1"; exit $? ;;
    probe) probe; exit $? ;;
    status)
        details=$(probe) || exit $?
        printf '%s\n' "$details" | awk '
            /^MODEL=/{m=substr($0,7)} /^DEVICE=/{d=substr($0,8)} /^SOC=/{s=substr($0,5)}
            /^PANEL=/{p=substr($0,7)} /^DATA_MD5=/{h=substr($0,10)} /^SYSMODE=/{v=substr($0,9)}
            /^PANEL_STATE=/{ps=substr($0,13)} /^PANEL_SOURCE=/{src=substr($0,14)} /^PANEL_TIMING=/{t=substr($0,14)}
            /^HARDWARE_OK=/{hw=substr($0,13)}
            /^---SF---$/{printf "Device: %s (%s, SoC=%s)\nPanel: %s (%s)\nPanel source: %s %s\nTarget match: %s\nJSON: %s\nSysMode: %s\n",
                m,d,s,(p?p:"unknown"),ps,(src?src:"unknown"),t,(hw==1 && ps=="match"?"yes":"no"),(h?"INSTALLED (" h ")":"NOT INSTALLED"),v; next}
            /DisplayColorSetting:|Current color mode:|ColorMode::/{print}'
        exit $? ;;
    install) src="/sdcard/Download/$panel_file"; if [ "$#" -gt 0 ] && [ "$1" != --force ]; then src=$1; shift; fi ;;
    uninstall) if [ "$#" -gt 0 ] && [ "$1" != --force ]; then proof=$1; shift; fi ;;
    mode) mode=${1:-}; case "$mode" in 0|1|2) shift ;; *) die 1 "Usage: mode 0|1|2" ;; esac ;;
    *) die 1 "Usage: status | install [profile.json] | mode 0|1|2 | uninstall [legacy.json]" ;;
esac
while [ "$#" -gt 0 ]; do
    case "$1" in
        --force) force=--force; shift ;;
        --sha256) [ "$op" = install ] && [ "$#" -ge 2 ] || die 1 "Missing transfer checksum"; expected=$2; shift 2 ;;
        *) die 1 "Unknown argument: $1" ;;
    esac
done
require_target
[ -r "$display_dir" ] && [ -x "$display_dir" ] || die 73 "Display directory is inaccessible"
# Lock the stable directory inode; there is no lock file to unlink or abandon.
(
    flock -nx 9 || die 5 "Calibration is busy or its lock is unavailable"
    stage=''
    new_state=0
    cleanup() {
        if [ -n "$stage" ]; then rm -f "$stage"; fi
        if [ "$new_state" = 1 ]; then rmdir "$state_dir" 2>/dev/null || :; fi
    }
    trap cleanup 0
    trap 'exit 1' HUP INT TERM
    if [ "$op" = mode ]; then
        set_checked "$mode" || die 4 "Could not verify display mode $mode"
        printf 'Display mode %s verified.\n' "$mode"
        exit 0
    fi
    current=$(target_hash) || die 73 "Cannot inspect the current display calibration"
    if [ "$op" = uninstall ] && [ "$current" = '-' ] && [ ! -e "$state_dir" ]; then
        printf '%s\n' 'UNCHANGED: no installed profile.'
        exit 0
    fi
    if [ ! -e "$state_dir" ]; then new_state=1; fi
    [ ! -L "$state_dir" ] && mkdir -p "$state_dir" && [ -x "$state_dir" ] ||
        die 73 "Cannot access installation state"
    if [ "$op" = install ]; then install_profile; else uninstall_profile; fi
) 9<"$display_dir"
